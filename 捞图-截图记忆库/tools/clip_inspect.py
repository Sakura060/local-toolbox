"""检查 CLIP ONNX 图的输入/输出签名（纯解析，绝不推理）。

背景（D 系列）：onnxruntime 推理在沙箱 detached 下必挂，但 onnx 包的
protobuf 解析与模型加载都不触发推理，安全。本工具回答两个问题：
  1. 图是"文+图合一"还是分塔（决定 embedder 要不要喂哑输入）；
  2. 输入名/dtype 与输出名（决定喂什么、取哪个输出）。

用法：python tools\\clip_inspect.py [onnx路径]
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT = PROJECT_ROOT / "data" / "models" / "chinese-clip-vit-base-patch16" / "onnx" / "model_quantized.onnx"


def via_onnx_package(path: Path) -> bool:
    try:
        import onnx
    except ImportError:
        return False
    model = onnx.load(str(path), load_external_data=False)
    g = model.graph
    print("[onnx 包解析]")
    print("inputs:")
    for i in g.input:
        dims = [d.dim_value if d.HasField("dim_value") else d.dim_param
                for d in i.type.tensor_type.shape.dim]
        print(f"  {i.name}  type={i.type.tensor_type.elem_type}  shape={dims}")
    print("outputs:")
    for o in g.output:
        dims = [d.dim_value if d.HasField("dim_value") else d.dim_param
                for d in o.type.tensor_type.shape.dim]
        print(f"  {o.name}  type={o.type.tensor_type.elem_type}  shape={dims}")
    opsets = ", ".join(
        f"{op.domain or 'ai.onnx'}:{op.version}" for op in model.opset_import)
    print(f"opset: {opsets}")
    return True


def via_ort_session(path: Path) -> None:
    """ORT 会话创建不触发推理（沙箱实证：挂的是 run），仅读签名。"""
    import onnxruntime as ort

    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    print("[ORT 会话签名（未 run）]")
    for i in sess.get_inputs():
        print(f"  in  {i.name}  {i.type}  {i.shape}")
    for o in sess.get_outputs():
        print(f"  out {o.name}  {o.type}  {o.shape}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not p.is_file():
        print(f"模型文件不存在：{p}\n先跑 python tools\\clip_download.py")
        return 2
    print(f"文件：{p}（{p.stat().st_size / 1e6:.0f} MB）\n")
    if not via_onnx_package(p):
        via_ort_session(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
