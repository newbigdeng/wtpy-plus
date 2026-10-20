"""Authenticated, user-scoped routes for the live-path historical runner."""
from pathlib import Path

from fastapi import Body, Request
from .Roles import RESEARCH_ROLES, can_research_read


def register_plus_routes(app, bt_mon, check_auth, manager=None):
    if manager is None:
        from .PlusBacktest import PlusBacktestManager
        manager = PlusBacktestManager(str(Path(bt_mon.path) / "_plus"))

    def authorize(request, token, read=False):
        authenticated, user = check_auth(request, token)
        if not authenticated:
            return None, user
        if not (can_research_read(user) if read else user.get("role") in RESEARCH_ROLES):
            return None, {"result": -403, "message": "需要策略研究权限"}
        return user, None

    def error_response(exc):
        return {"result": -2, "message": str(exc)}

    @app.post("/bt/plus/capabilities", tags=["Plus 仿真回测"])
    def capabilities(request: Request, token: str = Body(None, embed=True)):
        _, error = authorize(request, token, read=True)
        if error:
            return error
        return {"result": 0, "message": "OK", **manager.capabilities()}

    @app.post("/bt/plus/run", tags=["Plus 仿真回测"])
    def run(request: Request, straid: str = Body(..., embed=True),
            dataset: str = Body(..., embed=True), code: str = Body(..., embed=True),
            start: int = Body(None, embed=True), end: int = Body(None, embed=True),
            capital: float = Body(1000000, gt=0, le=1000000000, embed=True),
            period: str = Body("m1", embed=True),
            params: dict = Body(None, embed=True), finance: dict = Body(None, embed=True),
            execution: dict = Body(None, embed=True),
            token: str = Body(None, embed=True)):
        user, error = authorize(request, token)
        if error:
            return error
        login = user["loginid"]
        if not bt_mon.has_strategy(login, straid):
            return {"result": -2, "message": "策略不存在或不属于当前账号"}
        strategy = next((item for item in (bt_mon.get_strategies(login) or []) if item["id"] == straid), None)
        if strategy is None:
            return {"result": -2, "message": "策略不存在或不属于当前账号"}
        engine_type = str(strategy.get("strategy_type", "cta")).upper()
        if engine_type not in manager.capabilities().get("engines", []):
            return {"result": -3, "message": "当前 Plus 回测尚不支持 " + engine_type + "；请选择可用引擎"}
        source = bt_mon.get_strategy_code(login, straid)
        if source is None:
            return {"result": -2, "message": "请先保存策略代码"}
        try:
            task = manager.create(user=login, straid=straid, strategy_code=source,
                                  engine_type=engine_type, code=code, dataset=dataset,
                                  start=start, end=end, capital=capital, period=period,
                                  params=params, finance=finance, execution=execution)
        except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
            return error_response(exc)
        return {"result": 0, "message": "Plus 仿真任务已提交", "task": task}

    @app.post("/bt/plus/list", tags=["Plus 仿真回测"])
    def tasks(request: Request, straid: str = Body(None, embed=True),
              token: str = Body(None, embed=True)):
        user, error = authorize(request, token, read=True)
        if error:
            return error
        if straid is not None and not bt_mon.has_strategy(user["loginid"], straid):
            return {"result": -2, "message": "策略不存在或不属于当前账号"}
        return {"result": 0, "message": "OK", "tasks": manager.list_tasks(user["loginid"], straid)}

    @app.post("/bt/plus/detail", tags=["Plus 仿真回测"])
    def detail(request: Request, taskid: str = Body(..., embed=True),
               token: str = Body(None, embed=True)):
        user, error = authorize(request, token, read=True)
        if error:
            return error
        try:
            value = manager.detail(user["loginid"], taskid)
        except (OSError, ValueError, RuntimeError) as exc:
            return error_response(exc)
        if value is None:
            return {"result": -2, "message": "Plus 任务不存在或不属于当前账号"}
        return {"result": 0, "message": "OK", **value}

    @app.post("/bt/plus/stop", tags=["Plus 仿真回测"])
    def stop(request: Request, taskid: str = Body(..., embed=True),
             token: str = Body(None, embed=True)):
        user, error = authorize(request, token)
        if error:
            return error
        try:
            task = manager.stop(user["loginid"], taskid)
        except (OSError, ValueError, RuntimeError) as exc:
            return error_response(exc)
        if task is None:
            return {"result": -2, "message": "Plus 任务不存在或不属于当前账号"}
        return {"result": 0, "message": "停止请求已处理", "task": task}

    return manager
