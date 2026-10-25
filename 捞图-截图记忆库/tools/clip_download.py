"""下载中文 CLIP 模型到本地（捞图语义搜索的一次性准备）。

模型：Xenova/chinese-clip-vit-base-patch16（OFA-Sys 中文 CLIP 的 ONNX 转换，
     文/图双塔，512 维向量，支持中文查询）。
默认取 int8 量化版（190MB，CPU 推理最快）；--fp16 / --full 可换大版本。

用法（普通控制台即可，无需管理员）：
    cd 捞图-截图记忆库
    set HF_ENDPOINT=https://hf-mirror.com
    python tools\\clip_download.py

下载完成后：重启捞图（启动捞图.bat），语义搜索自动点亮。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = PROJECT_ROOT / "data" / "models" / "chinese-clip-vit-base-patch16"
REPO = "Xenova/chinese-clip-vit-base-patch16"

SMALL_FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
]
ONNX_VARIANT = {
    "quantized": "onnx/model_quantized.onnx",   # int8 ~191MB（默认）
    "fp16": "onnx/model_fp16.onnx",             # ~377MB
    "full": "onnx/model.onnx",                  # fp32 ~754MB
}


def download(variant: str, out_dir: Path) -> int:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("缺 huggingface_hub：pip install huggingface_hub")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    files = SMALL_FILES + [ONNX_VARIANT[variant]]
    print(f"仓库：{REPO}")
    print(f"版本：{variant}（{ONNX_VARIANT[variant]}）")
    print(f"目标：{out_dir}\n")

    failed: list[str] = []
    for rel in files:
        t0 = time.time()
        print(f"↓ {rel} …", flush=True)
        try:
            path = hf_hub_download(
                repo_id=REPO, filename=rel, local_dir=str(out_dir))
            size = Path(path).stat().st_size
            print(f"  ✓ {size / 1e6:.1f} MB（{time.time() - t0:.0f}s）")
        except Exception as exc:
            print(f"  ✗ {type(exc).__name__}: {exc}")
            failed.append(rel)

    if failed:
        print(f"\n{len(failed)} 个文件失败：{failed}")
        print("提示：确认 set HF_ENDPOINT=https://hf-mirror.com 后重试（脚本已自动设置）")
        return 1

    onnx_path = out_dir / ONNX_VARIANT[variant]
    print(f"\n全部完成 ✅ 模型目录：{out_dir}")
    print("现在重启捞图（启动捞图.bat），语义搜索会自动点亮。")
    print(f"（ONNX 文件：{onnx_path.name}）")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    variant = "quantized"
    args = sys.argv[1:]
    if "--fp16" in args:
        variant = "fp16"
    if "--full" in args:
        variant = "full"
    if "--dir" in args:
        out_dir = Path(args[args.index("--dir") + 1])
    else:
        out_dir = DEFAULT_DIR
    return download(variant, out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
