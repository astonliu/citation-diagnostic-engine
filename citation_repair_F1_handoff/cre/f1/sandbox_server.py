"""Local runner for the sandbox packet UI: the same page, wired to the engine.

WHY A LOCAL SERVER AND NOT A HOSTED PAGE. The UI in ``sandbox_ui.html`` can be
published as a static artifact, and there it can do exactly two useful things --
author a packet and read a record back. It cannot RUN one. A hosted page lives in
a sandboxed frame with no filesystem, no Python, no access to the frozen
authorities, and a content policy that blocks even a request to localhost. So the
hosted page's only honest offer is "copy this JSON, paste it into a terminal,
paste the output back", which is eight clipboard trips per question asked.

This module closes that loop by serving the SAME file from the machine the engine
already runs on, where the venv, the authorities and the key all are. The page
probes ``/api/status`` when it loads: served from here it finds a live engine and
shows a Run button; opened as an artifact it finds nothing and falls back to the
copy-paste path. One page, two contexts, no second UI to keep in sync.

WHAT IT IS NOT. It is not a service. It binds 127.0.0.1 only, and that is not a
default to be overridden by a flag -- this process holds an API key and spends
money on request, so a bind address reachable from the network would turn a bench
into an open relay for someone else's model calls. There is no auth, because
there is no listener but you.

NOTHING IS CACHED BETWEEN RUNS, and the authority load is the reason. An F7 run
re-hashes 16.6 GB and costs about twelve seconds before the first model call.
Memoizing that across a long-lived process is the obvious optimization and it is
refused here: the hash comparison is the gate that proves the frozen indexes have
not moved, and a run at hour three must not assert a hash that was checked at
hour zero. Twelve seconds is what the gate costs. ``--verify`` is exposed in the
UI because the engine already offers it as a decision, not as a speed dial.

Usage:
    python -m cre.f1.sandbox_server
    python -m cre.f1.sandbox_server --authorities /path/to/f7_authorities
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import sandbox_judge as sj
from . import sandbox_wiring as sw

UI_PATH = Path(__file__).with_name("sandbox_ui.html")

#: Where the key is read from when the environment does not carry one. Never
#: printed, never returned over the wire, and never passed on a command line --
#: an argv is visible in ``ps`` to every process on the machine.
KEY_FILE = Path.home() / ".cre_bench_key"

#: A run makes paid model calls, so one at a time. Two browser tabs racing would
#: interleave their calls in a single receipt and bill twice for one answer.
_RUN_LOCK = threading.Lock()

CONFIG = {"authorities": "", "model": "claude-opus-5"}


def read_key() -> str:
    """The key, from the environment or the key file. Returns "" if absent."""
    env = os.environ.get("ANTHROPIC_API_KEY") or ""
    if env.strip():
        return env.strip()
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def key_status() -> dict:
    """What the UI may know about the key: that there is one, and where from.

    Deliberately not the key, not a prefix, not a suffix. A status endpoint that
    returned four characters of a secret would be four characters of a secret in
    every browser log and every screenshot of this page.
    """
    if (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return {"present": True, "source": "ANTHROPIC_API_KEY"}
    if read_key():
        return {"present": True, "source": str(KEY_FILE)}
    return {"present": False, "source": ""}


def run_packet(body: dict) -> dict:
    """One packet through ``sandbox_judge.judge``. Returns the UI's envelope.

    Every failure is classified, because the three kinds mean different things to
    someone holding a packet: a REFUSAL is the packet's fault and is the engine
    working; a MODEL error is the provider's; anything else is a defect and keeps
    its traceback. Collapsing them into one "error" string would make a mistyped
    section label look like an outage.
    """
    packet = body.get("packet")
    if not isinstance(packet, dict):
        return {"ok": False, "kind": "packet",
                "error": "the request carried no packet object"}

    dry = bool(body.get("dry_run"))
    verify = body.get("verify") or "sqlite"
    if verify not in ("sqlite", "all", "none"):
        return {"ok": False, "kind": "packet",
                "error": f"verify must be sqlite, all or none, not {verify!r}"}

    key = "" if dry else read_key()
    if not dry and not key:
        return {"ok": False, "kind": "key",
                "error": f"no API key: set ANTHROPIC_API_KEY or put one in {KEY_FILE}"}

    # ONE RECEIPT PER RUN, and sandbox_judge memoizes it in a module global. The
    # bench was written as a one-shot process where that is the correct lifetime;
    # here the process outlives the run, so the memo is cleared BEFORE each one.
    # Without this every run after the first would report the first run's model
    # and accumulate every earlier run's calls into one ever-growing receipt.
    sj._RECEIPT.clear()

    started = time.time()
    # stdout is the record's channel in the CLI. Anything a seam prints would
    # otherwise land in the server's terminal interleaved with the access log.
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            result = sj.judge(
                packet,
                model=body.get("model") or CONFIG["model"],
                api_key=key,
                taxonomies=None,
                dry_run=dry,
                authorities_root=body.get("authorities") or CONFIG["authorities"],
                verify=verify,
            )
    except (sj.PacketError, sw.WiringError) as exc:
        # The engine refusing a packet is the engine working. It is not an error
        # state of this server and must not read as one in the UI.
        return {"ok": False, "kind": "refusal", "error": str(exc),
                "elapsed": round(time.time() - started, 2)}
    except Exception as exc:                                  # noqa: BLE001
        name = type(exc).__name__
        kind = "model" if name in ("APIStatusError", "APIConnectionError",
                                   "RateLimitError", "AuthenticationError",
                                   "BadRequestError", "APIError") else "engine"
        return {"ok": False, "kind": kind, "error": f"{name}: {exc}",
                "traceback": traceback.format_exc(),
                "elapsed": round(time.time() - started, 2)}

    return {"ok": True, "result": result,
            "elapsed": round(time.time() - started, 2),
            "stdout": buf.getvalue()}


class Handler(BaseHTTPRequestHandler):
    server_version = "cre-sandbox"

    def log_message(self, fmt, *args):
        # One line per request, without the default's date preamble.
        sys.stderr.write("  %s\n" % (fmt % args))

    # -- helpers ----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # This page is served to one browser on one machine. No other origin has
        # any business scripting it, and none is granted one.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    # -- routes -----------------------------------------------------------
    def do_GET(self):                                          # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                html = UI_PATH.read_bytes()
            except OSError as exc:
                self._send(500, f"cannot read {UI_PATH}: {exc}".encode(),
                           "text/plain; charset=utf-8")
                return
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(200, {
                "live": True,
                "key": key_status(),
                "model": CONFIG["model"],
                "authorities": CONFIG["authorities"],
                "cwd": os.getcwd(),
            })
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):                                         # noqa: N802
        if self.path.split("?")[0] != "/api/run":
            self._json(404, {"ok": False, "kind": "engine", "error": "no such route"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, OSError) as exc:
            self._json(400, {"ok": False, "kind": "packet",
                             "error": f"unreadable request body: {exc}"})
            return

        if not _RUN_LOCK.acquire(blocking=False):
            self._json(409, {"ok": False, "kind": "busy",
                             "error": "a run is already in flight; paid calls are "
                                      "serialized so two tabs cannot bill twice "
                                      "for one answer"})
            return
        try:
            self._json(200, run_packet(body))
        finally:
            _RUN_LOCK.release()


def pick_port(preferred: int) -> int:
    """``preferred`` if it is free, else the next free port above it."""
    for port in range(preferred, preferred + 40):
        with socket.socket() as s:
            # SAME OPTION THE SERVER ITSELF USES. HTTPServer sets
            # allow_reuse_address, so a port left in TIME_WAIT by the previous
            # run is bindable by the real listener; a probe socket without the
            # option would refuse it and silently move the URL one port along on
            # every restart, which is a papercut with a bookmark attached.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"no free port in {preferred}..{preferred + 39}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cre.f1.sandbox_server",
        description="Serve the packet UI and run packets through the real band.")
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--authorities", default="",
                        help="default folder for F7; the page can override it")
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a browser")
    args = parser.parse_args(argv)

    if not UI_PATH.exists():
        print(f"[sandbox-server] missing UI file: {UI_PATH}", file=sys.stderr)
        return 2
    # The namespace-package trap, caught at startup rather than as an import
    # error inside the first run: `cre` has no __init__, so it only resolves
    # from the handoff directory.
    if not Path("cre/f1").is_dir():
        print("[sandbox-server] run this from citation_repair_F1_handoff; "
              "`cre` is a namespace package and will not import from elsewhere",
              file=sys.stderr)
        return 2

    CONFIG["authorities"] = args.authorities
    CONFIG["model"] = args.model
    port = pick_port(args.port)
    url = f"http://127.0.0.1:{port}/"

    key = key_status()
    print(f"[sandbox-server] {url}")
    print(f"[sandbox-server] model {args.model}   key "
          f"{'from ' + key['source'] if key['present'] else 'NOT FOUND (dry runs only)'}")
    if args.authorities:
        print(f"[sandbox-server] authorities {args.authorities}")
    print("[sandbox-server] 127.0.0.1 only. Ctrl-C to stop.")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[sandbox-server] stopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
