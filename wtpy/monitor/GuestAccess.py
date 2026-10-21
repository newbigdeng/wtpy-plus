"""Explicit read-only boundary for the optional public guest session."""
import datetime
import json
import time
from collections import defaultdict, deque
from starlette.responses import JSONResponse

GUEST_MESSAGE = "游客模式不支持操作"
# POST is retained because the existing WT query APIs use JSON bodies.
GUEST_READ_PATHS = frozenset("""
/mgr/session /mgr/auth /mgr/qrygrp /mgr/qrystras /mgr/qrychnls
/mgr/qrytrds /mgr/qrysigs /mgr/qryrnds /mgr/qrypos /mgr/qryfunds
/mgr/qrychnlords /mgr/qrychnltrds /mgr/qrychnlpos /mgr/qrychnlfund
/mgr/qrymons /mgr/qrymon /mgr/qryusers /mgr/qryacts
/mgr/qrylogs /mgr/qrymonlog /mgr/qrygrpdir /mgr/qrygrpfile
/mgr/qryportpos /mgr/qryporttrd /mgr/qryportrnd /mgr/qryportfunds
/mgr/qryportperfs /mgr/qryportfilters /mgr/qrystra_runtime /mgr/qryrisk_state
/mgr/qrychannels /mgr/workbench /mgr/traderisk
/mgr/strategy_catalog /mgr/strategy_plan
/mgr/strategy_library /mgr/strategy_library_get
/mgr/history/catalog /mgr/history/datasets
/mgr/risk-library/list /mgr/risk-library/get
/mgr/market/subscriptions /mgr/market/providers /mgr/market/coverage /mgr/market/jobs
/bt/strategytypes /bt/qrystras /bt/qrycode /bt/qrybtdetail /bt/qrybars
/bt/qrystrabts /bt/qrybtsigs /bt/qrybttrds /bt/qrybtfunds /bt/qrybtrnds
/bt/qrycloses /bt/qryanalyst /bt/plus/capabilities /bt/plus/list /bt/plus/detail
""".split())
PUBLIC_PATHS = frozenset(("/mgr/login", "/mgr/guest", "/mgr/access", "/mgr/logout", "/hasbt"))
# These pages remain navigable without publishing private source, configuration or raw logs.
GUEST_EMPTY = {
    "/mgr/qrycode": {"content": "# 游客模式不公开策略源码\n"},
    "/bt/qrycode": {"content": "# 游客模式不公开策略源码\n"},
    "/mgr/qrygrpfile": {"content": "游客模式不公开配置文件。"},
    "/mgr/qrygrpdir": {"tree": {"label": "配置文件（仅账户所有者可查看）", "children": []}},
    "/mgr/qrylogs": {"logs": "游客模式不公开原始运行日志。", "lines": 0},
    "/mgr/qrymonlog": {"logs": "游客模式不公开原始调度日志。", "lines": 0},
    "/mgr/qryusers": {"users": [{"loginid": "owner", "name": "工作台所有者", "role": "admin", "remark": "仅所有者可管理账户"}]},
    "/mgr/qryacts": {"actions": []},
}
PRIVATE_KEYS = frozenset(("passwd", "password", "pass", "token", "secret", "seckey", "authcode", "appsecret", "brokerid", "userid", "user_id", "accountid", "account_id", "investorid", "loginip", "iplist", "loginid", "createby", "modifyby", "source_code", "strategy_code", "traceback", "log_tail", "exception", "investor", "user", "account", "auth", "error", "errors", "last_error", "stderr", "stdout", "output", "logs", "log", "config", "configuration", "broker", "address", "front", "tdfront", "mdfront", "source"))
PATH_KEYS = frozenset(("path", "folder", "root", "file", "filename", "argv", "command", "cmd", "param", "params", "environment", "env", "source_path", "worker_path", "config_path", "storage", "files", "storage_root", "data_root", "manifest", "manifest_path", "market_snapshot"))

def guest_session_valid(scope):
    info = scope.get("session", {}).get("tokeninfo") or {}
    if info.get("role") != "guest":
        return False
    settings = scope["app"].state
    return (getattr(settings, "guest_enabled", False)
            and info.get("auth_version") == getattr(settings, "auth_version", None)
            and info.get("expiretime", "") > datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S"))

def guest_data(value):
    if isinstance(value, list):
        return [guest_data(item) for item in value]
    if not isinstance(value, dict):
        return value
    clean = {}
    for key, item in value.items():
        lower = key.lower()
        if lower in PRIVATE_KEYS or lower in PATH_KEYS or "password" in lower or "secret" in lower or lower.endswith("_token"):
            continue
        clean[key] = False if lower.startswith("can_") else guest_data(item)
    if clean.get("result", 0) < 0:
        clean["message"] = "游客页面暂时无法读取这项数据"
    return clean

class LoginAttempts:
    """Bound failed attempts per peer and globally; forwarded headers are not trusted."""
    def __init__(self):
        self.by_peer = defaultdict(deque)
        self.global_failures = deque()
    def blocked(self, peer):
        now = time.monotonic()
        for queue in (self.global_failures, self.by_peer.get(peer, ())):
            while queue and queue[0] < now - 600:
                queue.popleft()
        return len(self.global_failures) >= 80 or len(self.by_peer.get(peer, ())) >= 8
    def record(self, peer, success):
        if success:
            self.by_peer.pop(peer, None)
        else:
            if len(self.by_peer) >= 1024:
                self.by_peer.pop(next(iter(self.by_peer)))
            self.by_peer[peer].append(time.monotonic())
            self.global_failures.append(time.monotonic())

class GuestAccessMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"].rstrip("/") or "/"
        is_api = path.startswith(("/mgr/", "/bt/"))
        info = scope.get("session", {}).get("tokeninfo") or {}
        guest = info.get("role") == "guest"
        public_view = any(key.lower() == b"x-wt-public-read-only" and value == b"1" for key, value in scope.get("headers", []))
        if is_api and public_view and (path == "/mgr/login" or (path not in ("/mgr/guest", "/mgr/access", "/mgr/logout") and not guest)):
            return await JSONResponse({"result": -403, "code": "GUEST_READ_ONLY", "message": "公开访问只提供游客浏览，请通过 SSH 管理工作台"}, 403)(scope, receive, send)
        if not is_api or path in PUBLIC_PATHS:
            async def public_send(message):
                if is_api and message["type"] == "http.response.start":
                    message["headers"] = list(message.get("headers", [])) + [(b"cache-control", b"no-store")]
                await send(message)
            return await self.app(scope, receive, public_send)
        if guest and not guest_session_valid(scope):
            return await JSONResponse({"result": -999, "message": "登录已过期"}, 401)(scope, receive, send)
        if not guest:
            # Owner-only mode rejects old non-owner sessions as well as forged API tokens.
            state = scope["app"].state
            if getattr(state, "guest_enabled", False) and (info.get("loginid") != state.monitor_owner or info.get("auth_version") != state.auth_version):
                return await JSONResponse({"result": -999, "message": "请先登录"}, 401)(scope, receive, send)
            headers = dict(scope.get("headers", []))
            origin = headers.get(b"origin", b"").decode("latin1")
            if scope["method"] not in ("GET", "HEAD", "OPTIONS") and origin:
                from urllib.parse import urlsplit
                if urlsplit(origin).netloc != headers.get(b"host", b"").decode("latin1"):
                    return await JSONResponse({"result": -403, "message": "拒绝跨站操作"}, 403)(scope, receive, send)
            return await self.app(scope, receive, send)
        if scope["method"] not in ("GET", "POST") or path not in GUEST_READ_PATHS:
            return await JSONResponse({"result": -403, "code": "GUEST_READ_ONLY", "message": GUEST_MESSAGE}, 403)(scope, receive, send)
        if path in GUEST_EMPTY:
            return await JSONResponse({"result": 0, "message": "OK", **GUEST_EMPTY[path]})(scope, receive, send)
        # Query options can request a network refresh, so deny them before the route runs.
        if path in ("/mgr/market/providers", "/mgr/market/subscriptions"):
            body = b""
            more = True
            while more:
                message = await receive()
                body += message.get("body", b"")
                more = message.get("more_body", False)
                if len(body) > 32768:
                    return await JSONResponse({"result": -403, "code": "GUEST_READ_ONLY", "message": GUEST_MESSAGE}, 403)(scope, receive, send)
            try:
                refresh = bool(json.loads(body or b"{}").get("refresh"))
            except (ValueError, AttributeError):
                refresh = False
            if refresh:
                return await JSONResponse({"result": -403, "code": "GUEST_READ_ONLY", "message": GUEST_MESSAGE}, 403)(scope, receive, send)
            delivered = False
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body}
                return await receive()
            route_receive = replay
        else:
            route_receive = receive
        start = None
        chunks = []
        async def capture(message):
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
        await self.app(scope, route_receive, capture)
        try:
            value = json.loads(b"".join(chunks))
            # Session metadata uses a deliberately tiny public shape.
            if path in ("/mgr/session", "/mgr/auth"):
                value = {"result": 0, "userinfo": {"loginid": "guest", "name": "游客", "role": "guest", "guest": True, "products": []}}
            else:
                value = guest_data(value)
            response = JSONResponse(value, status_code=start["status"])
            response.headers["Cache-Control"] = "no-store"
            return await response(scope, receive, send)
        except (ValueError, TypeError):
            return await JSONResponse({"result": -1, "message": "游客页面暂时无法读取这项数据"}, 500)(scope, receive, send)
