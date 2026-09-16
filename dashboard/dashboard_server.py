#!/usr/bin/env python3
"""dashboard_server.py — FastAPI + WebSocket status dashboard for PQ-VPN.

One background thread polls the server and client daemon IPC sockets; every
browser (WebSocket or GET /api/status) reads that cached snapshot, so N open
tabs cost one IPC poll per interval, not N. Mutating endpoints
(POST /disconnect/{id}, POST /rekey) require the dashboard token in an
`X-PQVPN-Token` header — the custom header forces a CORS preflight, which
closes CSRF from other origins, and the secret keeps a LAN-exposed dashboard
from being driven by strangers.

Run from the project root:
    .venv/bin/python3 dashboard/dashboard_server.py [--host 127.0.0.1] [--port 8080] [--token ...]

Token resolution: --token, then $PQVPN_DASH_TOKEN, else a random one is
generated at startup and printed once. It is never served by any endpoint.
"""
import os
import sys
import json
import time
import secrets
import threading
import argparse
from contextlib import asynccontextmanager

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from common.daemon_utils import ipc_request, socket_path, discover_sockets, instance_label

DASH_DIR = os.path.dirname(os.path.abspath(__file__))
POLL_INTERVAL = 0.5  # poll daemon status sockets every 0.5s -> <=1s UI updates
TOKEN_HEADER = 'X-PQVPN-Token'


def resolve_token(explicit: str | None = None) -> str:
    """--token > $PQVPN_DASH_TOKEN > freshly generated."""
    tok = explicit or os.environ.get('PQVPN_DASH_TOKEN', '').strip()
    return tok or secrets.token_urlsafe(24)


# Set once at import so tests can read it; main() may override from CLI/env.
DASH_TOKEN = resolve_token()


def token_ok(presented: str | None) -> bool:
    return bool(presented) and secrets.compare_digest(presented, DASH_TOKEN)


class StatusFeed:
    """Single poller: `_run` refreshes `_last`; readers call `latest()`."""

    def __init__(self, interval: float = POLL_INTERVAL):
        self.interval = interval
        self._stop = threading.Event()
        self._last = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def snapshot(self) -> dict:
        server = None
        clients = []
        if os.path.exists(socket_path('server')):
            try:
                server = ipc_request('server')
            except Exception:
                server = None
        for sp in discover_sockets('client'):
            try:
                s = ipc_request('client', instance=instance_label(sp) or None)
            except Exception:
                continue
            s['_instance'] = instance_label(sp)
            clients.append(s)
        # 'client' kept for UI backward compatibility (first/legacy instance);
        # 'clients' carries every running instance.
        return {'server': server, 'client': clients[0] if clients else None,
                'clients': clients, 'timestamp': time.time()}

    def latest(self) -> dict:
        """Cached snapshot from the poller; polls once if none exists yet."""
        with self._lock:
            last = self._last
        if last is None:
            last = self.snapshot()
            with self._lock:
                if self._last is None:
                    self._last = last
        return last

    def _run(self):
        while not self._stop.is_set():
            snap = self.snapshot()
            with self._lock:
                self._last = snap
            self._stop.wait(self.interval)


feed = StatusFeed()


@asynccontextmanager
async def lifespan(app: FastAPI):
    feed.start()
    try:
        yield
    finally:
        feed.stop()


app = FastAPI(title='PQ-VPN Dashboard', lifespan=lifespan)


@app.websocket('/ws/status')
async def ws_status(ws: WebSocket):
    import asyncio
    await ws.accept()
    last = None
    try:
        while True:
            snapshot = feed.latest()
            if snapshot != last:
                await ws.send_json(snapshot)
                last = snapshot
            await asyncio.sleep(POLL_INTERVAL)
    except WebSocketDisconnect:
        pass


@app.get('/api/status')
async def api_status():
    return JSONResponse(feed.latest())


def _unauthorized():
    return JSONResponse({'ok': False, 'error': 'unauthorized: missing or wrong '
                         f'{TOKEN_HEADER} header'}, status_code=401)


@app.post('/disconnect/{client_id}')
async def disconnect(client_id: int, x_pqvpn_token: str | None = Header(default=None)):
    if not token_ok(x_pqvpn_token):
        return _unauthorized()
    result = _server_command({'cmd': 'disconnect', 'client_id': client_id})
    if result is None:
        return JSONResponse({'ok': False, 'error': 'server not running'}, status_code=503)
    inner = result.get('result') or {}
    if not inner.get('ok'):
        return JSONResponse({'ok': False, 'error': inner.get('error', 'failed')}, status_code=404)
    return JSONResponse(inner)


@app.post('/rekey')
async def rekey(x_pqvpn_token: str | None = Header(default=None)):
    if not token_ok(x_pqvpn_token):
        return _unauthorized()
    # s_server does not support mid-stream renegotiation; a reconnect performs
    # a fresh handshake (optionally resumed from the ticket cache).
    return JSONResponse({
        'ok': False,
        'message': 'mid-stream rekey unsupported; reconnect to re-handshake',
    })


def _server_command(payload: dict) -> dict | None:
    if not os.path.exists(socket_path('server')):
        return None
    return ipc_request('server', command=json.dumps(payload))


app.mount('/', StaticFiles(directory=DASH_DIR, html=True), name='static')


def main():
    global DASH_TOKEN
    parser = argparse.ArgumentParser(description='PQ-VPN Dashboard')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--token', default=None,
                        help=f'shared secret for {TOKEN_HEADER} (default: $PQVPN_DASH_TOKEN or random)')
    args = parser.parse_args()
    DASH_TOKEN = resolve_token(args.token)

    import uvicorn
    print(f'[dashboard] serving http://{args.host}:{args.port} (status poll {POLL_INTERVAL}s)')
    print(f'[dashboard] token for kick/rekey ({TOKEN_HEADER}): {DASH_TOKEN}')
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        print('[dashboard] WARNING: bound to a non-loopback address — status is '
              'readable by anyone who can reach it; only the token guards actions')
    uvicorn.run(app, host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
