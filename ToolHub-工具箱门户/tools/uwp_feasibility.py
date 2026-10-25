"""UWP/商店应用导入可行性实测（总提示词阶段 3）。

测试范围（明确且最小）：只用系统自带"计算器"做启动验证，绝不枚举即启动。
产出：docs/UWP可行性报告.md 的原始数据。
"""
import subprocess
import time
from pathlib import Path

OUT = []


def log(msg):
    print(msg)
    OUT.append(msg)


def ps(script, timeout=30):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout)
    return r.returncode, r.stdout or "", r.stderr or ""


log("== 1. Get-StartApps 枚举 ==")
rc, out, err = ps("Get-StartApps | Measure-Object | Select-Object -ExpandProperty Count")
log(f"总条目: {out.strip()} (rc={rc})")
rc, out, err = ps("(Get-StartApps | Where-Object {$_.AppID -like '*!*'} | Measure-Object).Count")
log(f"AppID 含 '!'（UWP/MSIX 特征）的条目: {out.strip()}")
rc, out, err = ps("Get-StartApps | Where-Object {$_.AppID -like '*!*'} | Select-Object -First 8 | Format-Table -AutoSize | Out-String -Width 200")
log("样例:\n" + out)

log("== 2. 计算器 AUMID 定位 ==")
rc, out, err = ps("(Get-StartApps | Where-Object {$_.Name -like '*计算器*' -or $_.Name -like '*Calculator*'} | Select-Object -First 1).AppID")
aumid = out.strip()
log(f"AUMID = {aumid!r} (rc={rc})")

if aumid:
    log("== 3. 启动实测（explorer shell:appsFolder）==")
    rc, out, err = ps(f"Start-Process 'shell:appsFolder\\{aumid}'")
    log(f"Start-Process rc={rc} err={err.strip()[:200]}")
    time.sleep(3)
    rc, out, err = ps("(Get-Process -Name CalculatorApp -ErrorAction SilentlyContinue | Measure-Object).Count")
    log(f"3 秒后 CalculatorApp 进程数: {out.strip()}")
    log("== 4. 收尾：关闭本次测试启动的计算器 ==")
    rc, out, err = ps("Stop-Process -Name CalculatorApp -ErrorAction SilentlyContinue; Start-Sleep 1; (Get-Process -Name CalculatorApp -ErrorAction SilentlyContinue | Measure-Object).Count")
    log(f"关闭后进程数: {out.strip()}")

log("== 5. 图标可行性（Get-AppxPackage Logo 路径读取）==")
if aumid and "!" in aumid:
    family = aumid.split("!")[0]
    rc, out, err = ps(f"(Get-AppxPackage -Name '{family.split('_')[0]}' | Select-Object -First 1).InstallLocation")
    loc = out.strip()
    log(f"InstallLocation = {loc!r}")
    if loc:
        manifest = Path(loc) / "AppxManifest.xml"
        log(f"manifest 存在: {manifest.is_file()}")
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
            import re
            logos = re.findall(r'Square44x44Logo="([^"]+)"', text)
            log(f"Square44 logo 声明: {logos[:3]}")
            p = Path(loc) / (logos[0] if logos else "")
            log(f"logo 文件可读: {p.is_file()}（WindowsApps 目录 ACL 可能拒绝普通读取）")
        except OSError as exc:
            log(f"读 manifest 失败: {exc}")

Path(__file__).with_name("uwp_feasibility_raw.txt").write_text(
    "\n".join(OUT), encoding="utf-8")
print("\nRAW SAVED")
