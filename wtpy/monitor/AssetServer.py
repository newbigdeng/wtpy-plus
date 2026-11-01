"""Serve /mgr/assets/ beside an existing WT monitor, sharing its session validation."""
from contextlib import asynccontextmanager
import asyncio
import logging
import os

from fastapi import FastAPI
from .AssetWorkspace import register_asset_routes
from .ResearchServer import monitor_auth


class AssetAudit:
    def log_action(self, user, action, target):
        logging.getLogger("wt.assets").info("user=%s action=%s target=%s", user["loginid"], action, target)


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        yield
        for monitor in managers.values():
            await asyncio.to_thread(monitor.shutdown)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    upstream = os.environ.get("WT_MONITOR_UPSTREAM", "http://127.0.0.1:8100")

    def authenticate(request, token):
        ok, user = monitor_auth(upstream, request)
        if ok and (user.get("role") == "guest" or user.get("products")):
            return False, {"result": -403, "message": "此资产工作区需要独立授权账户"}
        return ok, user

    managers = register_asset_routes(app, AssetAudit(), authenticate, os.environ.get("WT_DATA_DIR", "/data"))
    return app
