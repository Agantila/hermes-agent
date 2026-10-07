"""Remote config: a whole-document save from an older read never undoes a newer write, a Cloud
(Nous credential) boot in a fresh interpreter does not hang, a plane URL is expanded once across
dotenv reloads, a provider switch never half-applies its route, a deleted profile stops being
polled, and a named custom provider added on the plane is seen after a poll."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from hermes_cli.config_backend import get_config_backend, read_config_doc, write_config_document

from .conftest import _config_cmd
from .stub_plane import INSTANCE


def test_cloud_boot_in_a_fresh_interpreter_does_not_hang(plane, tmp_path):
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    (plane.home / "auth.json").write_text(json.dumps({"version": 1, "active_provider": "nous", "providers": {
        "nous": {"access_token": "plane-token-1", "expires_at": expires, "refresh_token": "r"}}}))
    plane.profile("default").update(values={"display": {"personality": "remote"}}, version=1)
    child = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TMPDIR", "SYSTEMROOT")}
    child.update(HERMES_HOME=str(plane.home), HOME=str(tmp_path), HERMES_CONFIG_BACKEND="remote",
                 HERMES_CONFIG_REMOTE_URL=plane.url, HERMES_CONFIG_INSTANCE_ID=INSTANCE,
                 PYTHONPATH=str(Path(__file__).resolve().parents[3]))
    code = ("from hermes_cli.env_loader import load_hermes_dotenv\n"
            "load_hermes_dotenv(load_external_secrets=False)\n"
            "import tui_gateway.server\n"
            "from hermes_cli.config import load_config\n"
            "print('RESULT=' + load_config()['display']['personality'])\n")

    proc = subprocess.run([sys.executable, "-c", code], env=child, capture_output=True, text=True,
                          timeout=90, stdin=subprocess.DEVNULL, cwd=str(tmp_path))

    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "RESULT=remote" in proc.stdout + proc.stderr  # tui_gateway.server routes print() to stderr


def test_reloading_dotenv_expands_a_plane_url_once(plane, monkeypatch):
    from hermes_cli.env_loader import load_hermes_dotenv
    monkeypatch.setenv("HERMES_CONFIG_REMOTE_URL", plane.url)
    (plane.home / ".env").write_text("HERMES_CONFIG_REMOTE_URL=${HERMES_CONFIG_REMOTE_URL}/\n")

    for _ in range(3):
        load_hermes_dotenv(hermes_home=plane.home, load_external_secrets=False)

    assert os.environ["HERMES_CONFIG_REMOTE_URL"] == plane.url + "/"
