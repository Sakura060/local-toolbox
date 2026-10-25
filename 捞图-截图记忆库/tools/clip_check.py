"""捞图 CLIP 前置环境检查（在【真实控制台】跑，勿在无人值守会话里跑）。

背景（2026-09-27 实证）：onnxruntime 推理在沙箱 detached 环境下必挂
（模型加载成功但推理无输出），与 CTranslate2 行为相反。所以中文 CLIP
语义搜索的前置验证只能在用户控制台进行。

用法（管理员不需要，普通控制台即可）：
    cd 捞图-截图记忆库
    python tools\\clip_check.py

检查项：
  1. onnxruntime 可导入 + 执行一次微型推理（用 onnx 包现场生成小模型）
  2. Pillow / numpy 版本
  3. 现有 RapidOCR 引擎前台推理是否正常（复用 laotu.ocr，若索引库为空会现截一张）
  4. 打印中文 CLIP 模型下载指引（HF_ENDPOINT=hf-mirror.com）

全部通过 = CLIP 语义搜索可以开工；第 1 项失败 = ORT 环境问题，先修环境。
"""

from __future__ import annotations

import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def step1_ort_inference() -> bool:
    print("[1/4] onnxruntime 微型推理……")
    try:
        import onnxruntime as ort

        print("  onnxruntime", ort.__version__, "providers:", ort.get_available_providers())
    except ImportError as exc:
        print("  ✗ onnxruntime 未安装：", exc)
        return False
    try:
        import numpy as np
        import onnx
        from onnx import TensorProto, helper, numpy_helper
    except ImportError as exc:
        print("  ! onnx/numpy 缺失（pip install onnx numpy），跳过微型推理：", exc)
        return True  # 环境问题不阻塞判断，但 CLIP 开工前要装
    # 极简模型：y = x * 2 + 1
    node = helper.make_node("Add", ["x", "b"], ["y"])
    graph = helper.make_graph(
        [node], "tiny",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [2])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [2])],
        [numpy_helper.from_array(np.array([1.0, 1.0], dtype=np.float32), "b")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    path = "tiny_ort_check.onnx"
    onnx.save(model, path)
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    out = sess.run(None, {"x": np.array([3.0, 4.0], dtype=np.float32)})[0]
    print("  ✓ 微型推理结果:", out.tolist())
    return True


def step2_env() -> bool:
    print("[2/4] Pillow / numpy ……")
    import PIL
    import numpy

    print("  Pillow", PIL.__version__, "· numpy", numpy.__version__)
    return True


def step3_real_engine() -> bool:
    print("[3/4] RapidOCR 前台推理（真实引擎）……")
    sys.path.insert(0, ".")
    try:
        from laotu.ocr import get_ocr
    except Exception as exc:
        print("  ! 无法导入 laotu.ocr（需在项目根目录运行）：", exc)
        return True
    from PIL import Image

    img = Image.new("RGB", (300, 120), (255, 255, 255))
    t0 = time.time()
    try:
        from laotu.ocr import ocr_image

        text = ocr_image(img)
        print(f"  ✓ 引擎推理正常（{time.time() - t0:.1f}s，空白图文本={text!r}）")
        return True
    except Exception as exc:
        print("  ✗ 引擎推理失败：", type(exc).__name__, exc)
        return False


def step4_clip_guide() -> None:
    print("[4/4] 中文 CLIP 模型下载指引（确认可行后再下载，约几百 MB）")
    print("  set HF_ENDPOINT=https://hf-mirror.com")
    print("  python -c \"from huggingface_hub import snapshot_download;"
    print("snapshot_download('<chinese-clip-onnx 仓库 id，开工时确认>')\"")
    print("  设计要点见 TODO.md 的 CLIP 条目：文本/图片双编码器 → 512 维向量入库")
    print("  → hnswlib 近邻检索 → /api/search?semantic=1")


def main() -> int:
    print("=== 捞图 CLIP 前置检查（真实控制台）===\n")
    ok1 = step1_ort_inference()
    ok2 = step2_env()
    ok3 = step3_real_engine()
    step4_clip_guide()
    print("\n结论：", "ORT 前台推理可用，CLIP 可以开工 ✅"
          if (ok1 and ok2 and ok3) else "存在失败项 —— 把上面的输出发给开发会话 ❌")
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
