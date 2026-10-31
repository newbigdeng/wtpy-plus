"""Shared attempt budget for RD-Agent and interactive research."""
import fcntl
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo


def reserve_call(path, limit):
    """Count attempts before dispatch, including failures and interrupted calls."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        value = json.loads(path.read_text()) if path.exists() else {}
        if value.get('date') != today:
            value = dict(date=today, attempts=0)
        if value['attempts'] >= limit:
            raise RuntimeError('Daily RD-Agent call budget exhausted; wait until the next day')
        value['attempts'] += 1
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value))
        temporary.chmod(0o600)
        temporary.replace(path)
