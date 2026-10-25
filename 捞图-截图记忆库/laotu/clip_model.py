"""真 CLIP 引擎：onnxruntime + tokenizers（无 transformers/torch 依赖）。

目标模型：Xenova/chinese-clip-vit-base-patch16（OFA-Sys 中文 CLIP 的
ONNX 转换）。实证签名（tools/clip_inspect.py，2026-09-29）：
  inputs:  input_ids int64 · pixel_values float32 · attention_mask int64
  outputs: text_embeds [n_text,512] · image_embeds [n_img,512]（另有 logits）
合一图：两路输入都必喂——查文本时喂哑图，嵌图片时喂哑文本
（batch 维独立，哑批=1）。若将来换成文/图分塔的导出，检测逻辑自动适配。

运行红线：embed_* 里的 sess.run 只允许在真实控制台/用户机器上发生；
沙箱测试只测预处理、分词、IO 检测与哑输入构造，绝不调用 run。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .semantic import EmbedUnavailable

MODEL_NAME = "chinese-clip-vit-base-patch16"
ONNX_REL = "onnx/model_quantized.onnx"
TOKENIZER_REL = "tokenizer.json"

# 与仓库 preprocessor_config.json 对齐：resize 到 224（bicubic，不裁剪）、
# /255、双塔各自 mean/std（OpenAI CLIP 同款值）
IMAGE_SIZE = 224
IMAGE_MEAN = (0.48145466, 0.4578275, 0.40821073)
IMAGE_STD = (0.26862954, 0.26130258, 0.27577711)

DEFAULT_MAX_TOKENS = 52  # tokenizer_config.json 的 model_max_length


def _dtype_of(ort_type: str) -> np.dtype:
    """'tensor(int64)' → np.int64；未知类型报错（宁可早炸不给哑数据）。"""
    kind = ort_type.removeprefix("tensor(").removesuffix(")")
    try:
        return np.dtype(kind)
    except TypeError as exc:
        raise EmbedUnavailable(f"不支持的 ONNX 输入类型：{ort_type}") from exc


@dataclass
class _IO:
    """从 ORT 会话读出的输入/输出名与 dtype。"""

    inputs: dict[str, np.dtype] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)


def _read_io(sess) -> _IO:
    return _IO(
        inputs={i.name: _dtype_of(i.type) for i in sess.get_inputs()},
        outputs=[o.name for o in sess.get_outputs()],
    )


def _pick_output(names: list[str], *keywords: str) -> str:
    for n in names:
        for kw in keywords:
            if kw in n:
                return n
    raise EmbedUnavailable(
        f"ONNX 输出里找不到 {keywords}：实际有 {names}")


class CLIPEmbedder:
    """中文 CLIP 文/图双塔 embedder。"""

    def __init__(self, model_dir: Path, onnx_rel: str = ONNX_REL,
                 tokenizer_rel: str = TOKENIZER_REL,
                 max_tokens: int | None = None) -> None:
        import onnxruntime as ort

        self.model_dir = Path(model_dir)
        self.name = MODEL_NAME
        onnx_path = self.model_dir / onnx_rel
        tok_path = self.model_dir / tokenizer_rel
        if not onnx_path.is_file():
            raise EmbedUnavailable(
                f"缺模型文件 {onnx_path}——先跑 tools\\clip_download.py")
        if not tok_path.is_file():
            raise EmbedUnavailable(
                f"缺分词文件 {tok_path}——先跑 tools\\clip_download.py")

        if max_tokens is None:
            max_tokens = self._model_max_tokens(tok_path.parent)
        self.max_tokens = max(8, min(int(max_tokens), 512))

        self._tok = self._load_tokenizer(tok_path)
        self._sess = ort.InferenceSession(
            str(onnx_path), providers=["CPUExecutionProvider"])
        io = _read_io(self._sess)
        self._io = io
        self._out_text = _pick_output(io.outputs, "text_embeds")
        self._out_image = _pick_output(io.outputs, "image_embeds")
        self._combined = "input_ids" in io.inputs and "pixel_values" in io.inputs
        # 哑输入：合一图缺一不可（查文本喂哑图，嵌图片喂哑文本）
        self._dummy_pixel = np.zeros(
            (1, 3, IMAGE_SIZE, IMAGE_SIZE), dtype=np.float32)
        self._dummy_ids, self._dummy_mask = self._pack_ids(
            self._tok.encode("").ids)
        self._dim = 512  # 首次推理后按真实输出修正（哑输入无法提前知道）

    # ---------- 加载细节 ----------

    @staticmethod
    def _model_max_tokens(model_dir: Path) -> int:
        cfg = model_dir / "tokenizer_config.json"
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            return int(data.get("model_max_length") or DEFAULT_MAX_TOKENS)
        except (OSError, ValueError):
            return DEFAULT_MAX_TOKENS

    @staticmethod
    def _load_tokenizer(tok_path: Path):
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbedUnavailable(
                "缺 tokenizers 包：pip install tokenizers") from exc
        return Tokenizer.from_file(str(tok_path))

    # ---------- 编码 ----------

    def _pack_ids(self, ids: list[int]) -> tuple[np.ndarray, np.ndarray]:
        """截断（保留末尾 SEP，BERT 后处理器恒以 SEP 收尾）+ 补零到
        max_tokens，dtype 按 ONNX 图要求（实证 int64），形状 [1, L]。"""
        orig = list(ids)
        if len(orig) > self.max_tokens:
            orig = orig[: self.max_tokens - 1] + [orig[-1]]
        mask = [1] * len(orig) + [0] * (self.max_tokens - len(orig))
        padded = orig + [0] * (self.max_tokens - len(orig))
        dt = self._io.inputs.get("input_ids", np.dtype("int64"))
        md = self._io.inputs.get("attention_mask", np.dtype("int64"))
        return (
            np.asarray([padded], dtype=dt),
            np.asarray([mask], dtype=md),
        )

    def _preprocess(self, img: Image.Image) -> np.ndarray:
        """PIL → float32 [1,3,224,224]，squash resize + CLIP 归一。"""
        img = img.convert("RGB").resize(
            (IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BICUBIC)
        x = np.asarray(img, dtype=np.float32) / 255.0
        x = (x - np.asarray(IMAGE_MEAN, dtype=np.float32)) / np.asarray(
            IMAGE_STD, dtype=np.float32)
        return np.ascontiguousarray(x.transpose(2, 0, 1))[None]

    @staticmethod
    def _l2(v: np.ndarray) -> np.ndarray:
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    # ---------- 对外接口（内含推理，只在真实机器调用） ----------

    def embed_text(self, text: str) -> np.ndarray:
        ids, mask = self._pack_ids(self._tok.encode(text).ids)
        feed = {"input_ids": ids, "attention_mask": mask}
        if self._combined:
            feed["pixel_values"] = self._dummy_pixel
        out = self._sess.run([self._out_text], feed)[0]
        return self._l2(np.asarray(out[0], dtype=np.float32))

    def embed_image(self, path: str | Path) -> np.ndarray:
        vecs = self.embed_images([path])
        return vecs[0]

    def embed_images(self, paths, batch_size: int = 8) -> np.ndarray:
        """批量图片 → [N, dim] 已归一。文件缺失跳过（返回行=False 占位
        由调用方过滤：这里用 NaN 行表示失败）。"""
        out_rows: list[np.ndarray] = []
        chunk: list[tuple[str, np.ndarray]] = []
        for p in paths:
            try:
                with Image.open(p) as im:
                    px = self._preprocess(im)
                chunk.append((str(p), px))
            except (OSError, ValueError):
                out_rows.append(np.full(self.vector_dim, np.nan, dtype=np.float32))
                continue
            if len(chunk) >= batch_size:
                out_rows.extend(self._run_image_chunk(chunk))
                chunk = []
        if chunk:
            out_rows.extend(self._run_image_chunk(chunk))
        return np.stack(out_rows)

    @property
    def vector_dim(self) -> int:
        if self._dim is None:
            return 512
        return self._dim

    def _run_image_chunk(self, chunk) -> list[np.ndarray]:
        pixels = np.concatenate([px for _, px in chunk], axis=0)
        feed = {"pixel_values": pixels.astype(self._io.inputs.get(
            "pixel_values", np.dtype("float32")))}
        if self._combined:
            feed["input_ids"] = self._dummy_ids
            feed["attention_mask"] = self._dummy_mask
        out = self._sess.run([self._out_image], feed)[0]
        if self._dim is None:
            self._dim = int(out.shape[-1])
        return [self._l2(np.asarray(row, dtype=np.float32)) for row in out]


def probe(model_dir: Path) -> tuple[CLIPEmbedder | None, str]:
    """探测语义引擎可用性；可用则返回就绪 embedder，否则返回 (None, 原因)。

    成本：ORT 会话创建（约 1~2s，不推理）。调用方需缓存结果。
    """
    d = Path(model_dir)
    try:
        return CLIPEmbedder(d), ""
    except EmbedUnavailable as exc:
        return None, str(exc)
    except ImportError:
        return None, "缺 onnxruntime：pip install onnxruntime"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
