#!/usr/bin/env python3
"""Tiny config backend for the OpenBB Umbrel app.

Serves a handful of JSON endpoints (behind the app's caddy front door, which is
itself behind Umbrel auth):

  GET  /status       -> current provider keys (names only), external toggle, endpoint
  POST /credentials  -> {"name": "fmp_api_key", "value": "..."}  (value "" removes)
  POST /external     -> {"enabled": true|false}                  (toggles the flag file)

No external deps, stdlib only. Runs as uid 1000.
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SETTINGS = os.environ.get("OPENBB_SETTINGS_FILE", "/data/.openbb_platform/user_settings.json")
FLAGS_DIR = os.environ.get("FLAGS_DIR", "/flags")
FLAG = os.path.join(FLAGS_DIR, "external.enabled")
TOKEN = os.environ.get("EXTERNAL_TOKEN", "")
EXTERNAL_PORT = os.environ.get("EXTERNAL_PORT", "")
PORT = int(os.environ.get("CONFIG_PORT", "8000"))

NAME_RE = re.compile(r"^[a-z0-9_]{2,64}$")


def load():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {"credentials": {}, "preferences": {}, "defaults": {"commands": {}}}


def save(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SETTINGS)


def set_credential(name, value):
    """Add/update (value set) or remove (value empty) a provider credential."""
    if not NAME_RE.match(name or ""):
        raise ValueError("invalid credential name")
    data = load()
    creds = data.setdefault("credentials", {})
    if value:
        creds[name] = value
    else:
        creds.pop(name, None)
    save(data)
    return sorted(k for k, v in creds.items() if v)


def set_external(enabled):
    if enabled:
        os.makedirs(FLAGS_DIR, exist_ok=True)
        open(FLAG, "w").close()
    else:
        try:
            os.remove(FLAG)
        except FileNotFoundError:
            pass
    return os.path.exists(FLAG)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/status", ""):
            creds = (load().get("credentials") or {})
            self._json(200, {
                "providers": sorted(k for k, v in creds.items() if v),
                "external_enabled": os.path.exists(FLAG),
                "external_port": EXTERNAL_PORT,
                "token": TOKEN,
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"error": "invalid json"})
        path = self.path.rstrip("/")
        try:
            if path == "/credentials":
                providers = set_credential(str(body.get("name", "")).strip().lower(),
                                           str(body.get("value", "")).strip())
                return self._json(200, {"providers": providers})
            if path == "/external":
                return self._json(200, {"external_enabled": set_external(bool(body.get("enabled")))})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


def _selftest():
    import tempfile
    global SETTINGS, FLAGS_DIR, FLAG
    d = tempfile.mkdtemp()
    SETTINGS = os.path.join(d, ".openbb_platform", "user_settings.json")
    FLAGS_DIR = os.path.join(d, "flags")
    FLAG = os.path.join(FLAGS_DIR, "external.enabled")

    assert set_credential("fmp_api_key", "abc") == ["fmp_api_key"]
    assert load()["credentials"]["fmp_api_key"] == "abc"
    assert set_credential("fmp_api_key", "") == []          # removal
    assert "fmp_api_key" not in load()["credentials"]
    try:
        set_credential("BAD NAME", "x"); assert False
    except ValueError:
        pass
    assert set_external(True) is True and os.path.exists(FLAG)
    assert set_external(False) is False and not os.path.exists(FLAG)
    print("selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
