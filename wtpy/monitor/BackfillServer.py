"""Standalone history importer: no strategy runner, collector or trading endpoints."""
import os
from fastapi import FastAPI
from .AssetBackfill import register, SOURCES
from .ResearchServer import monitor_auth
from .Roles import can_read


def create_app():
    app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    def authorize(request,asset):
        ok,user = monitor_auth(os.environ.get("WT_MONITOR_UPSTREAM","http://127.0.0.1:8100"),request)
        if not ok:return None,user
        if not can_read(user) or user.get("role")=="guest" or user.get("products"):
            return None,dict(result=-403,message="此资产工作区需要独立授权账户")
        if asset not in SOURCES:return None,dict(result=-2,message="请选择股票、商品期权或加密货币")
        return user,None
    register(app,os.environ.get("WT_DATA_DIR","/data"),authorize)
    return app
