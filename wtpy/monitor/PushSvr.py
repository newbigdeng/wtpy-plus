from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from .WtLogger import WtLogger
import asyncio
import json
import threading
import datetime
from .Roles import group_allowed
from .GuestAccess import guest_session_valid, guest_data


class PushServer:
    """All WebSocket IO stays on the ASGI event loop; producers may call from threads."""

    def __init__(self, app: FastAPI, dataMgr, logger: WtLogger = None):
        self.app, self.dataMgr, self.logger = app, dataMgr, logger
        self.active_connections = []
        self.ready = False
        self.stopped = False
        self.worker = None
        self._loop = None
        self._queue = None
        self._registered = False
        self._pending = []
        self._pending_lock = threading.Lock()

    async def start(self):
        with self._pending_lock:
            self._queue = asyncio.Queue()
            self._loop = asyncio.get_running_loop()
            for item in self._pending:
                self._queue.put_nowait(item)
            self._pending.clear()
            self.stopped = False
            self.worker = asyncio.create_task(self.loop())
            self.ready = True

    async def stop(self):
        self.ready = False
        self.stopped = True
        if self.worker:
            self._queue.put_nowait(None)
            await self.worker
            self.worker = None
        for ws in list(self.active_connections):
            try:
                await ws.close(code=1001)
            except (RuntimeError, WebSocketDisconnect, OSError):
                pass
        self.active_connections.clear()
        self._loop = None

    @staticmethod
    def authorized(ws):
        info = ws.session.get("tokeninfo") or {}
        if info.get("role") == "guest":
            return guest_session_valid(ws.scope)
        state = ws.app.state
        if getattr(state, "guest_enabled", False):
            return (info.get("loginid") == state.monitor_owner
                    and info.get("auth_version") == state.auth_version
                    and info.get("expiretime", "") > datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")
                    and not any(key.lower() == b"x-wt-public-read-only" and value == b"1" for key, value in ws.scope.get("headers", [])))
        return bool(info) and info.get("expiretime", "") > datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")

    async def connect(self, ws: WebSocket):
        if not self.authorized(ws):
            await ws.close(code=1008)
            return False
        origin = ws.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != ws.headers.get("host"):
                await ws.close(code=1008)
                return False
        await ws.accept()
        self.active_connections.append(ws)
        if self.logger:
            self.logger.info(f"{ws.session['tokeninfo']['loginid']} connected")
        return True

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)
            if self.logger and ws.session.get("tokeninfo") is not None:
                self.logger.info(f"{ws.session['tokeninfo']['loginid']} disconnected")

    @staticmethod
    async def send_personal_message(data: dict, ws: WebSocket):
        await ws.send_json(data)

    async def _broadcast(self, data: dict, groupid: str = ""):
        groupid = groupid or data.get("groupid", "")
        async def send(ws):
            try:
                if not self.authorized(ws):
                    self.disconnect(ws)
                    await ws.close(code=1008)
                    return
                info = ws.session["tokeninfo"]
                if groupid and not group_allowed(info, groupid):
                    return
                if info.get("role") == "guest":
                    if data.get("type") != "gpevt":
                        return
                    payload = {"type": "gpevt", "groupid": data.get("groupid", ""),
                               "evttype": data.get("evttype") if data.get("evttype") in ("start", "stop") else "updated"}
                else:
                    payload = data
                await asyncio.wait_for(ws.send_json(payload), timeout=5)
            except (RuntimeError, WebSocketDisconnect, OSError, TimeoutError):
                self.disconnect(ws)
        recipients = [ws for ws in list(self.active_connections)
                      if not groupid or ws.session.get("groupid") in (None, groupid)]
        await asyncio.gather(*(send(ws) for ws in recipients))

    def broadcast(self, data: dict, groupid: str = ""):
        self._enqueue(data, groupid)

    def _enqueue(self, data, groupid=""):
        with self._pending_lock:
            if not self.ready:
                return
            loop = self._loop
            if loop is None:
                self._pending.append((data, groupid))
            elif not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(self._queue.put_nowait, (data, groupid))
                except RuntimeError:
                    pass  # Shutdown completed between the readiness check and enqueue.

    def on_subscribe_group(self, ws: WebSocket, data: dict):
        if ws not in self.active_connections or "groupid" not in data:
            return
        if not group_allowed(ws.session["tokeninfo"], data["groupid"]):
            return
        ws.session["groupid"] = data["groupid"]
        if self.logger:
            token = ws.session["tokeninfo"]
            self.logger.info("{}@{} subscribed group {}".format(token["loginid"], token.get("loginip", "guest"), data["groupid"]))

    def run(self):
        if self._registered:
            return
        @self.app.websocket("/")
        async def ws_listen(ws: WebSocket):
            if not await self.connect(ws):
                return
            try:
                while True:
                    try:
                        raw = await ws.receive_text()
                        if len(raw) > 4096 or not self.authorized(ws):
                            await ws.close(code=1008)
                            break
                        req = json.loads(raw)
                        if not isinstance(req, dict):
                            continue
                        if req.get("type") == "subscribe":
                            self.on_subscribe_group(ws, req)
                            await self.send_personal_message(req, ws)
                        elif req.get("type") == "heartbeat":
                            await self.send_personal_message({"type": "heartbeat", "message": "pong"}, ws)
                    except (ValueError, KeyError, TypeError):
                        continue
            except (WebSocketDisconnect, RuntimeError, OSError):
                pass
            finally:
                self.disconnect(ws)
        self._registered = True
        self.ready = True

    async def loop(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            await self._broadcast(*item)

    def notifyGrpLog(self, groupid, tag: str, time: int, message):
        self._enqueue({"type": "gplog", "groupid": groupid, "tag": tag, "time": time, "message": message}, groupid)

    def notifyGrpEvt(self, groupid, evttype):
        self._enqueue({"type": "gpevt", "groupid": groupid, "evttype": evttype})

    def notifyGrpChnlEvt(self, groupid, chnlid, evttype, data):
        self._enqueue({"type": "chnlevt", "groupid": groupid, "channel": chnlid, "data": data, "evttype": evttype})
