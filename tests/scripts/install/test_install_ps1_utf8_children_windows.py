"""install.ps1 pins every child to UTF-8, so non-ASCII child output survives (#132531).

On a zh-CN Windows the console is cp936 and Python's redirected stdio follows
the ANSI code page, so `pm`'s "…" / "✓" lines reached bootstrap-installer.log
as mojibake -- or killed the child with UnicodeEncodeError where the ANSI page
cannot encode them at all. This runs the real install.ps1 python-deps stage in
a real PowerShell child whose console encoding is cp936 (as on zh-CN), with
uv staged at its pinned store slot (a compiled fixture: no download) and a
pm-shaped package whose `pm.cli` is real Python printing non-ASCII text, then
reads the installer's raw stdout bytes the way Hermes-Setup does.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.platforms("windows")

REPO = Path(__file__).resolve().parents[3]
TEXT = "Preparing the isolated Hermes runtime\u2026 \u2713 \u62d2\u7edd\u8bbf\u95ee\u3002"
FAKE_UV = r"""
using System;
class Uv {
    static int Main(string[] a) {
        if (a.Length > 0 && a[0] == "--version") { Console.WriteLine("uv 99.0.0 (fixture)"); return 0; }
        if (a.Length > 1 && a[0] == "python" && a[1] == "find") {
            Console.WriteLine(Environment.GetEnvironmentVariable("UTF8_FIXTURE_PYTHON"));
            return 0;
        }
        return 3;
    }
}
"""
PM_CLI = f"""import sys
print({TEXT!r})
print("stdout encoding:", sys.stdout.encoding)
"""


def _shell(name: str) -> str:
    if name == "powershell":
        return str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    return shutil.which("pwsh") or pytest.fail("native lane requires PowerShell 7")


def _stage_fixture(root: Path) -> tuple[Path, Path, Path]:
    install_dir = root / "hermes-agent"
    (install_dir / "pm").mkdir(parents=True)
    (install_dir / "pm" / "lock.json").write_text('{"packages":{"python":{"version":"3.14.0"}}}', encoding="utf-8")
    (install_dir / "pm" / "__init__.py").write_text("", encoding="utf-8")
    (install_dir / "pm" / "cli.py").write_text(PM_CLI, encoding="utf-8")
    runtime = root / "tools"
    source = root / "uv.cs"
    source.write_text(FAKE_UV, encoding="utf-8")
    built = root / "uv.exe"
    csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(csc), "/nologo", "/target:exe", f"/out:{built}", str(source)],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    # Both arches: the stage asks the registry which machine this is.
    for arch in ("x64", "arm64"):
        slot = runtime / f"uv-0.12.3-win32-{arch}"
        slot.mkdir(parents=True)
        shutil.copy2(built, slot / "uv.exe")
    return install_dir, root / "home", runtime


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_python_child_output_survives_a_cp936_console(tmp_path, shell):
    install_dir, home, runtime = _stage_fixture(tmp_path)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONLEGACYWINDOWSSTDIO")}
    env.update(HERMES_RUNTIME_DIR=str(runtime), HERMES_HOME=str(home),
               UTF8_FIXTURE_PYTHON=getattr(sys, "_base_executable", sys.executable))
    command = ("[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(936); "
               f"& '{REPO / 'scripts' / 'install.ps1'}' -Stage python-deps -NonInteractive "
               f"-InstallDir '{install_dir}' -HermesHome '{home}'")
    result = subprocess.run(
        [_shell(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, env=env, stdin=subprocess.DEVNULL, timeout=300,
        # Hermes-Setup spawns install.ps1 hidden with piped stdio.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    report = f"exit {result.returncode}\nstdout:\n{stdout}\nstderr:\n{result.stderr.decode('utf-8', 'replace')}"
    assert result.returncode == 0, report
    assert TEXT in result.stdout.decode("utf-8"), report
