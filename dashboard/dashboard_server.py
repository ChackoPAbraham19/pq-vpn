#!/usr/bin/env python3
"""dashboard_server.py — FastAPI + WebSocket status dashboard for PQ-VPN.

Polls the server and client daemon IPC sockets and pushes snapshots to any
connected browser over /ws/status. Also exposes POST /disconnect/{id} and
POST /rekey that relay commands to the server daemon.

Run from the project root:
    .venv/bin/python3 dashboard/dashboard_server.py [--host 127.0.0.1] [--port 8080]
"""
import os
import sys
import json
import time
import threading
import argparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from common.daemon_utils import ipc_request, socket_path, discover_sockets, instance_label

DASH_DIR = os.path.dirname(os.path.abspath(__file__))
POLL_INTERVAL = 0.5  # poll daemon status sockets every 0.5s -> <=1s UI updates


class StatusFeed:
    def __init__(self, interval: float = POLL_INTERVAL):
        self.interval = interval
        self._stop = threading.Event()
        self._last = None
        self._thread: threading.Thread | None = None

    def start(self):
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

    def _run(self):
        # Pre-compute a snapshot so the first WS connect has something to send.
        self._last = self.snapshot()
        while not self._stop.is_set():
            self._last = self.snapshot()
            time.sleep(self.interval)


feed = StatusFeed()

app = FastAPI(title='PQ-VPN Dashboard')


@app.on_event('startup')
def _start():
    feed.start()


@app.on_event('shutdown')
def _stop():
    feed.stop()


@app.websocket('/ws/status')
async def ws_status(ws: WebSocket):
    import asyncio
    await ws.accept()
    last = None
    try:
        while True:
            snapshot = feed.snapshot()
            if snapshot != last:
                await ws.send_json(snapshot)
                last = snapshot
            await asyncio.sleep(POLL_INTERVAL)
    except WebSocketDisconnect:
        pass


@app.get('/api/status')
async def api_status():
    return JSONResponse(feed.snapshot())


@app.post('/disconnect/{client_id}')
async def disconnect(client_id: int):
    result = _server_command({'cmd': 'disconnect', 'client_id': client_id})
    if result is None:
        return JSONResponse({'ok': False, 'error': 'server not running'}, status_code=503)
    inner = result.get('result') or {}
    if not inner.get('ok'):
        return JSONResponse({'ok': False, 'error': inner.get('error', 'failed')}, status_code=404)
    return JSONResponse(inner)


@app.post('/rekey')
async def rekey():
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
    parser = argparse.ArgumentParser(description='PQ-VPN Dashboard')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()

    import uvicorn
    print(f'[dashboard] serving http://{args.host}:{args.port} (status poll {POLL_INTERVAL}s)')
    uvicorn.run(app, host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
