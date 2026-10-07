"""install.ps1 names a launch refusal instead of re-downloading a "broken" uv (#60132).

On hosts where AppLocker / App Control (WDAC) / an execute-deny ACL refuses
programs in user-writable folders, `& uv.exe --version` fails with "Access is
denied". The installer read that as a broken uv, deleted it, downloaded the
same bytes into the same blocked folder and then failed with the generic
"pinned uv staged but does not run on this host". This stages a real uv-shaped
exe at its pinned store slot, denies Everyone execute on that folder (the same
CreateProcess ERROR_ACCESS_DENIED the report shows), and runs the real
install.ps1 python-deps stage in a real PowerShell child.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.platforms("windows")

REPO = Path(__file__).resolve().parents[3]
FAKE_UV = r"""
class Uv { static int Main(string[] a) { System.Console.WriteLine("uv 99.0.0 (fixture)"); return 0; } }
"""
EVERYONE = "*S-1-1-0"


def _shell(name: str) -> str:
    if name == "powershell":
        return str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    return shutil.which("pwsh") or pytest.fail("native lane requires PowerShell 7")


def _icacls(*args: str) -> None:
    subprocess.run(["icacls", *args], check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_refused_uv_launch_is_named_and_not_redownloaded(tmp_path, shell):
    install_dir, home, runtime = tmp_path / "hermes-agent", tmp_path / "home", tmp_path / "tools"
    (install_dir / "pm").mkdir(parents=True)
    (install_dir / "pm" / "lock.json").write_text('{"packages":{"python":{"version":"3.14.0"}}}', encoding="utf-8")
    source = tmp_path / "uv.cs"
    source.write_text(FAKE_UV, encoding="utf-8")
    built = tmp_path / "uv.exe"
    csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(csc), "/nologo", "/target:exe", f"/out:{built}", str(source)],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    slots = [runtime / f"uv-0.12.3-win32-{arch}" for arch in ("x64", "arm64")]
    for slot in slots:
        slot.mkdir(parents=True)
        shutil.copy2(built, slot / "uv.exe")
        _icacls(str(slot), "/deny", f"{EVERYONE}:(OI)(CI)(X)")
    env = dict(os.environ, HERMES_RUNTIME_DIR=str(runtime), HERMES_HOME=str(home))
    command = (f"& '{REPO / 'scripts' / 'install.ps1'}' -Stage python-deps -NonInteractive "
               f"-InstallDir '{install_dir}' -HermesHome '{home}'")
    try:
        result = subprocess.run(
            [_shell(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, env=env, stdin=subprocess.DEVNULL, timeout=600,
        )
    finally:
        for slot in slots:
            _icacls(str(slot), "/remove:d", EVERYONE, "/T")
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    report = f"exit {result.returncode}\n{output}"
    assert result.returncode == 1, report
    assert "Windows refused to start" in output, report
    assert "AppLocker" in output and "uv-0.12.3-win32-" in output, report
    assert "downloading uv" not in output, report
