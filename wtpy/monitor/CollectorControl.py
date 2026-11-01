"""Manual subscription lists and the independent recorder for each asset."""
import argparse
from contextlib import suppress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from wtpy.StrategyRuntime import atomic_json
from .AssetMarkets import asset_root, inside, instrument
from .AssetCollector import native_config

ASSETS = ("option", "crypto")
DEFAULT = {"symbols": [], "enabled": False, "revision": 0}


def read(path, default):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return default


def state(root):
    hub = inside(root, "datahub")
    plan = read(hub/"subscriptions.json", DEFAULT)
    worker = read(hub/"status.json", {})
    if time.time() - worker.get("heartbeat", 0) > 15:
        worker = {"state": "offline"}
    elif worker.get("revision") != plan["revision"]:
        worker = {"state": "pending"}
    return {**plan, **worker}


def change(root, asset, symbols, action, revision):
    import fcntl
    hub = inside(root, "datahub")
    hub.mkdir(parents=True, exist_ok=True)
    with (hub/"settings.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = state(root)
        if old["revision"] != revision:
            raise ValueError("订阅已在其他页面修改，请刷新")
        if action == "stop":
            symbols, enabled = old["symbols"], False
        else:
            symbols = list(dict.fromkeys(s.strip() for s in symbols))
            if len(symbols) > 96:
                raise ValueError("每个采集器最多选择 96 个合约")
            for s in symbols:
                instrument(asset, s)
            if symbols and asset == "option":
                native_config(root, asset, symbols)
            enabled = action == "start"
            if enabled and (not symbols or old["state"] == "offline"):
                raise ValueError("请先选择标的并确认采集服务在线")
            if old["enabled"] and old["state"] != "failed":
                raise ValueError("请先停止当前采集，再修改订阅")
        atomic_json(hub/"subscriptions.json", dict(symbols=symbols, enabled=enabled, revision=revision+1))
        return state(root)


def supervise(root, asset):
    """The supervisor owns just one child. A failed selection stays visible until retried."""
    hub = inside(root, "datahub")
    hub.mkdir(parents=True, exist_ok=True)
    closed = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: closed.set())
    child, applied, error = None, None, None

    def stop_child():
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    try:
        while not closed.is_set():
            plan = read(hub/"subscriptions.json", DEFAULT)
            if plan["revision"] != applied:
                stop_child()
                child, error, applied = None, None, plan["revision"]
                if plan["enabled"]:
                    child = subprocess.Popen([sys.executable, "-u", "-m", "wtpy.monitor.AssetCollector",
                                              asset, *plan["symbols"]], cwd=hub)
            if child and child.poll() is not None:
                error = "采集器已退出，退出码 " + str(child.returncode)
                child = None
            status = dict(revision=applied, state="failed" if error else "running" if child else "stopped",
                          heartbeat=time.time(), message=error or "")
            atomic_json(hub/"status.json", status)
            closed.wait(2)
    finally:
        stop_child()
        with suppress(FileNotFoundError):
            (hub/"status.json").unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", choices=ASSETS)
    args = parser.parse_args()
    if args.asset == "crypto" and (secret := os.getenv("WT_COINBASE_PROXY_FILE")):
        os.environ["HTTPS_PROXY"] = Path(secret).read_text().strip()
    supervise(asset_root(os.getenv("WT_DATA_DIR", "/data"), args.asset), args.asset)
