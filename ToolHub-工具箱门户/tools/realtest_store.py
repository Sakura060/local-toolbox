"""真机深挖：商店应用走产品代码全链路（probe→commit→launch 真弹计算器→确认→关闭→移除）。"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT.parent))
sys.path.insert(0, str(PROJECT))

from desktop import plugins as P  # noqa: E402

AUMID = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))
    return ok


def calc_procs():
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process -Name CalculatorApp -ErrorAction SilentlyContinue | Measure-Object).Count"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    return int((r.stdout or "0").strip() or 0)


def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    pdir = Path(tmp.name) / "plugins"
    pdir.mkdir(parents=True)
    ok_all = True

    before = calc_procs()
    check("baseline: no calculator running", before == 0, f"{before} 个")

    # 1. probe（name_hint）
    r = P.probe_path(f"shell:appsfolder\\{AUMID}", pdir, name_hint="计算器")
    m = r["manifest"]
    ok_all &= check("probe store", m["kind"] == "store" and m["name"] == "计算器")
    ok_all &= check("probe errors empty", r["errors"] == [], str(r["errors"]))

    # 2. commit（字母占位图标生成）
    P.save_plugin(m, pdir, {"draft": False, "enabled": True})
    icon = P.plugin_dir(m["id"], pdir) / "icon.png"
    if not icon.is_file():
        P.letter_icon(m["name"], "", str(icon))
    ok_all &= check("commit + letter icon", icon.is_file())

    # 3. 状态视图（store 永远 ok）
    runner = P.PluginRunner(pdir)
    ok_all &= check("state ok", runner.state_view(m)["state"] == "ok")

    # 4. 产品启动路径（os.startfile shell:appsFolder）→ 真弹计算器
    res = runner.launch(P.load_plugin(m["id"], pdir))
    ok_all &= check("launch via product code", res.get("ok") is True, str(res))
    time.sleep(3.5)
    n = calc_procs()
    ok_all &= check("calculator really started", n >= 1, f"{n} 个进程")

    # 5. 状态统计已记录（run_count=1）
    st = P.get_state(m["id"], pdir)
    ok_all &= check("run_count recorded", st.get("run_count") == 1, str(st))

    # 6. 收尾：关闭计算器 + 移除插件登记
    subprocess.run(["taskkill", "/IM", "CalculatorApp.exe", "/F"],
                   capture_output=True, timeout=15)
    time.sleep(1)
    ok_all &= check("calculator closed", calc_procs() == 0)
    ok_all &= check("plugin removed", P.remove_plugin(m["id"], pdir))
    tmp.cleanup()
    print("ALL OK" if ok_all else "HAS FAILURES")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
