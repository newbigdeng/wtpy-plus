"""Read-only strategy cards from completed, correctly associated task snapshots."""
from datetime import datetime
import math
from pathlib import Path

from .PlusBacktest import read_json
from .PlusReport import read_view


def _saved_code_matches(current, snapshot):
    if not current.is_file() or not snapshot.is_file():
        return None
    return current.read_bytes() == snapshot.read_bytes()


def _timestamp(task):
    if isinstance(task.get("created_at"), (int, float)):
        return task["created_at"]
    try:
        return datetime.strptime(task["runtime"], "%Y.%m.%d %H:%M:%S").timestamp()
    except (KeyError, TypeError, ValueError):
        return 0


def merge_latest_performance(strategies, user, *, research_root, plus_manager=None):
    """Decorate authorized strategy rows; never edit source indexes or task ledgers.

    user is the authenticated data owner, not a browser-supplied owner. Only
    that owner's marker and Plus tasks may contribute. Historical source is
    explicitly marked when it differs from the currently saved editor source.
    """
    if user in ("", ".", "..") or Path(user).name != user:
        raise ValueError("Invalid research owner")
    root = Path(research_root)/user
    marker = read_json(root/"marker.json", {}) or {}
    tasks = plus_manager.list_tasks(user) if plus_manager else []
    result = []
    for original in strategies:
        row = dict(original)
        straid = row["id"]
        if straid in ("", ".", "..") or Path(straid).name != straid:
            raise ValueError("Invalid strategy identifier")
        folder = root/straid
        candidates = []
        for identifier, task in marker.get("backtests", {}).items():
            if identifier in ("", ".", "..") or Path(identifier).name != identifier or task.get("status") != "completed":
                continue
            run = folder/"backtests"/identifier
            if task.get("straid") != straid and not (task.get("straid") is None and run.is_dir()):
                continue
            summary = read_json(run/"outputs_bt"/identifier/"summary.json", {}) or {}
            if not all(isinstance(summary.get(key), (int, float)) and math.isfinite(summary[key])
                       for key in ("total_return", "max_falldown")):
                continue
            candidates.append({"task_id": identifier, "mode": "fast", "created_at": _timestamp(task),
                               "metrics": summary, "start": task.get("state", {}).get("stime"),
                               "end": task.get("state", {}).get("etime"),
                               "matches_saved_code": _saved_code_matches(folder/"MyStrategy.py", run/"MyStrategy.py")})
        completed = [task for task in tasks if task.get("straid") == straid and task.get("status") == "completed"]
        for task in sorted(completed, key=_timestamp, reverse=True):
            run = plus_manager.root/task["id"]
            cache = plus_manager.cache_root/task["id"] if plus_manager.cache_root else None
            view = read_view(run, cache)
            if view.get("performance") is not None:
                candidates.append({"task_id": task["id"], "mode": "plus", "created_at": _timestamp(task),
                                   "metrics": view["performance"], "start": task.get("start"), "end": task.get("end"),
                                   "matches_saved_code": _saved_code_matches(folder/"MyStrategy.py", run/"MyStrategy.py")})
                break
        row["latest_performance"] = max(candidates, key=lambda item: item["created_at"]) if candidates else None
        result.append(row)
    return result
