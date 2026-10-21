"""Versioned strategy sources shared by research and stopped portfolio plans."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading

from fastapi import Body, Request
from wtpy.StrategyRuntime import atomic_json
from .Roles import can_admin
from .StrategySource import analyze, identifier

library_lock = threading.RLock()


class StrategyLibrary:
    def __init__(self, root):
        self.root = Path(root)

    @classmethod
    def for_deployment(cls, deploy_dir):
        return cls(os.environ.get("WT_STRATEGY_LIBRARY_ROOT") or Path(deploy_dir) / "strategy-library")

    @contextmanager
    def locked(self):
        import fcntl
        with library_lock:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            with (self.root / ".library.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield

    def _index(self):
        path = self.root / "library.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema": 1, "strategies": {}}

    def list(self, engine_type=None):
        return sorted((dict(row) for row in self._index()["strategies"].values()
                       if not row.get("archived") and (not engine_type or row["engine_type"] == engine_type.upper())),
                      key=lambda row: (row["engine_type"], row["name"], row["id"]))

    def get(self, library_id, version=None, allow_archived=True):
        library_id = identifier(library_id)
        item = self._index()["strategies"].get(library_id)
        if not item or (item.get("archived") and not allow_archived):
            raise ValueError("策略库条目不存在或已归档")
        version = item["version"] if version is None else version
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("请选择有效的策略库版本")
        folder = self.root / library_id / "versions" / str(version)
        if not (folder / "strategy.json").is_file():
            raise ValueError("策略库版本不存在")
        meta = json.loads((folder / "strategy.json").read_text(encoding="utf-8"))
        return {**meta, "archived": item.get("archived", False),
                "source": (folder / "strategy.py").read_text(encoding="utf-8")}

    def save(self, library_id, name, source, description="", base_version=None):
        library_id = identifier(library_id)
        source = source.removeprefix("\ufeff")
        name = name.strip()
        if not name or len(name) > 80 or len(description) > 2000:
            raise ValueError("请填写80字内的策略名称，说明不超过2000字")
        cls = analyze(source)[0]
        with self.locked():
            index = self._index()
            previous = index["strategies"].get(library_id)
            if previous and previous.get("archived"):
                raise ValueError("此ID已归档，请使用新ID，旧版本仍供历史计划读取")
            if previous and base_version != previous["version"]:
                raise ValueError("策略已被其他页面更新，请重新读取后保存")
            if not previous and base_version is not None:
                raise ValueError("策略不存在，请重新读取策略库")
            if previous:
                old = self.get(library_id)
                if all(old.get(key) == value for key, value in (("source", source), ("name", name), ("description", description))):
                    return old
                if old["engine_type"] != cls["engine_type"]:
                    raise ValueError("已有策略不能更换引擎类型，请创建新的库条目")
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            version = previous["version"] + 1 if previous else 1
            while (self.root / library_id / "versions" / str(version)).exists():
                version += 1
            meta = {"id": library_id, "name": name, "description": description,
                    "version": version, "class_name": cls["name"], "engine_type": cls["engine_type"],
                    "parameters": cls["parameters"], "accepts_kwargs": cls["accepts_kwargs"],
                    "supported": cls["supported"], "created_at": previous["created_at"] if previous else stamp,
                    "updated_at": stamp, "archived": False}
            folder = self.root / library_id / "versions" / str(version)
            folder.mkdir(mode=0o700, parents=True)
            path = folder / "strategy.py"
            path.write_text(source, encoding="utf-8")
            path.chmod(0o600)
            atomic_json(folder / "strategy.json", meta)
            index["strategies"][library_id] = meta
            atomic_json(self.root / "library.json", index)
            return {**meta, "source": source}

    def archive(self, library_id, base_version):
        library_id = identifier(library_id)
        with self.locked():
            index = self._index()
            row = index["strategies"].get(library_id)
            if not row or row.get("archived"):
                raise ValueError("策略不存在或已归档")
            if base_version != row["version"]:
                raise ValueError("策略已更新，请刷新策略库再删除")
            row["archived"] = True
            atomic_json(self.root / "library.json", index)


def register_library_routes(app, data_mgr, check_auth, deploy_dir, bt_mon_provider=None):
    library = StrategyLibrary.for_deployment(deploy_dir)
    app.state.strategy_library = library

    def authorize(request, token, write=False):
        ok, info = check_auth(request, token)
        if not ok:
            return None, info
        if write and not can_admin(info):
            return None, {"result": -403, "message": "仅管理员可修改策略库"}
        return info, None

    @app.post("/mgr/strategy_library", tags=["策略库"])
    def listing(request: Request, engine_type: str = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token)
        if error:
            return error
        rows = library.list(engine_type)
        if not can_admin(info):
            for row in rows:
                row.pop("parameters", None)
                row.pop("accepts_kwargs", None)
        return {"result": 0, "strategies": rows, "can_manage": can_admin(info)}

    @app.post("/mgr/strategy_library_get", tags=["策略库"])
    def get(request: Request, library_id: str = Body(..., embed=True), version: int = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token)
        if error:
            return error
        try:
            row = library.get(library_id, version)
            if not can_admin(info):
                row.pop("source", None)
                row.pop("parameters", None)
                row.pop("accepts_kwargs", None)
            return {"result": 0, "strategy": row, "can_manage": can_admin(info)}
        except (OSError, ValueError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc, ValueError) else "策略版本无法读取"}

    @app.post("/mgr/strategy_library_template", tags=["策略库"])
    def template(request: Request, engine_type: str = Body("CTA", embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, True)
        if error:
            return error
        from .BacktestTemplates import strategy_template
        try:
            return {"result": 0, "source": strategy_template(engine_type.lower())}
        except ValueError as exc:
            return {"result": -2, "message": str(exc)}

    @app.post("/mgr/strategy_library_save", tags=["策略库"])
    def save(request: Request, library_id: str = Body(..., embed=True), name: str = Body(..., embed=True), source: str = Body(..., embed=True), description: str = Body("", embed=True), base_version: int = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, True)
        if error:
            return error
        try:
            row = library.save(library_id, name, source, description, base_version)
            data_mgr.log_action(info, "strategy_library_save", library_id + "/v" + str(row["version"]))
            return {"result": 0, "strategy": row, "message": "已保存策略库版本，已有组合和研究快照保持原版本"}
        except (OSError, ValueError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc, ValueError) else "策略版本保存失败"}

    @app.post("/mgr/strategy_library_delete", tags=["策略库"])
    def remove(request: Request, library_id: str = Body(..., embed=True), base_version: int = Body(..., embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, True)
        if error:
            return error
        try:
            library.archive(library_id, base_version)
            data_mgr.log_action(info, "strategy_library_archive", library_id)
            return {"result": 0, "message": "已从策略库移除；已有组合及历史版本保留"}
        except (OSError, ValueError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc, ValueError) else "策略删除失败"}

    @app.post("/mgr/strategy_library_research", tags=["策略库"])
    def research(request: Request, token: str = Body(None, embed=True)):
        info, error = authorize(request, token, True)
        if error:
            return error
        monitor = bt_mon_provider() if bt_mon_provider else None
        rows = monitor.get_strategies(info["loginid"]) if monitor else []
        return {"result": 0, "strategies": [{"id": row["id"], "name": row["name"],
            "engine_type": row.get("strategy_type", "cta").upper()} for row in rows]}

    @app.post("/mgr/strategy_library_import_research", tags=["策略库"])
    def import_research(request: Request, research_id: str = Body(..., embed=True), library_id: str = Body(..., embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, True)
        if error:
            return error
        monitor = bt_mon_provider() if bt_mon_provider else None
        try:
            existing = next((row for row in monitor.get_strategies(info["loginid"]) if row["id"] == research_id), None) if monitor else None
            if not existing:
                raise ValueError("当前账户没有这个研究策略")
            source = monitor.get_strategy_code(info["loginid"], research_id)
            row = library.save(library_id, existing["name"], source,
                "从在线研究“%s”复制；研究ID：%s。后续修改互不影响。" % (existing["name"], research_id))
            data_mgr.log_action(info, "strategy_library_import_research", library_id + "/" + research_id)
            return {"result": 0, "strategy": row, "message": "研究策略已复制到策略库，原研究和结果保留"}
        except (OSError, ValueError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc, ValueError) else "研究源码无法读取"}
