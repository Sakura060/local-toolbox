"""Renumber my duplicate D18/D19 entries to D20/D21 and fix cross-references."""
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "DECISIONS.md"
t = d.read_text(encoding="utf-8")
t = t.replace("## D18 多会话并行协调规则（2026-09-27，重要）",
              "## D20 多会话并行协调规则（2026-09-27，重要）")
t = t.replace("## D19 捞图 v0.2 相似查重设计（2026-09-27）",
              "## D21 捞图 v0.2 相似查重设计（2026-09-27）")
d.write_text(t, encoding="utf-8")

todo = Path(__file__).resolve().parent.parent / "TODO.md"
t = todo.read_text(encoding="utf-8")
t = t.replace("**多会话协调（D18）**", "**多会话协调（D20）**")
todo.write_text(t, encoding="utf-8")
print("renumbered to D20/D21")
