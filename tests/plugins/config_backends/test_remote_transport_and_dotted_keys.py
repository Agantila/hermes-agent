"""Remote config: the plane bearer never follows a redirect to another origin, and a dotted key
lands where the file backend puts it (an existing literal ``grok-4.6`` key is not split)."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hermes_cli.config_backend import write_config_key
from plugins.config_backends import remote as remote_pkg
from plugins.config_backends.remote import backend as backend_mod
from plugins.config_backends.remote import client
from plugins.config_backends.remote import credentials as cred_mod

from .stub_plane import StubPlane, remote_env


def _serve(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_plane_bearer_is_not_forwarded_across_a_redirect(monkeypatch):
    seen = {}

    class Other(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 — base signature
            pass

        def do_GET(self):
            seen["authorization"] = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    other = _serve(Other)

    class Plane(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 — base signature
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{other.server_port}/elsewhere")
            self.end_headers()

    plane = _serve(Plane)
    try:
        monkeypatch.setenv(client.URL_ENV, f"http://127.0.0.1:{plane.server_port}")
        monkeypatch.setattr(client, "plane_token", lambda home: "plane-bearer")
        client.request("GET", Path("/unused"), "default")
    finally:
        plane.shutdown()
        other.shutdown()
    assert "authorization" in seen  # the redirect was followed
    assert seen["authorization"] is None


@pytest.fixture
def plane(tmp_path, monkeypatch):
    home = Path(tmp_path) / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    with StubPlane() as p:
        for k, v in remote_env(p).items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(backend_mod, "BOOT_RETRY_DELAYS", (0.0, 0.0))
        remote_pkg._reset_for_tests()
        cred_mod._IDP_CACHE.clear()
        p.home = home
        yield p
        remote_pkg._reset_for_tests()
        cred_mod._IDP_CACHE.clear()


def test_dotted_key_lands_on_the_existing_literal_key(plane):
    plane.profile("default").update(values={"models": {"grok-4.6": {"ctx": 1}}}, version=1)

    write_config_key(plane.home / "config.yaml", "models.grok-4.6.supports_vision", True)

    assert plane.profile("default")["values"] == {"models": {"grok-4.6": {"ctx": 1, "supports_vision": True}}}
