"""File-backed shared memory for a live analysis session and its WT strategies."""
import json
import mmap
from math import isfinite
import os
from pathlib import Path
import re
import time


def publish_frame(directory, frame):
    data = json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(",",":")).encode()
    # Existing readers finish their old mapping. New opens see one complete generation.
    pending = Path(directory)/"frame.next"
    with pending.open("w+b") as out:
        out.truncate(len(data))
        with mmap.mmap(out.fileno(), len(data), access=mmap.ACCESS_WRITE) as buf:
            buf[:] = data
    os.replace(pending, pending.with_name("frame"))


def read_option_frame(option_root, session, *, mode, max_age=10, symbols=()):
    """Read from the same option volume as the producer; a stopped session cannot be reused."""
    if not re.fullmatch(r"[0-9a-f]{24}", session):
        raise ValueError("分析会话编号无效")
    file = Path(option_root)/"analysis"/session/"frame"
    with file.open("rb") as src, mmap.mmap(src.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        value = json.loads(buf[:])
    now = time.time()
    if value["mode"] != mode or not 0<=now-value["published"]<=max_age:
        raise ValueError("分析快照已过期或行情环境不一致")
    usable = {r["symbol"] for r in value["rows"] if not r.get("reason") and
              isfinite(r.get("iv") or 0) and (r.get("iv") or 0)>0 and r.get("valid_until",0)>=now}
    if set(symbols)-usable:
        raise ValueError("缺少有效盘口或 IV：" + ", ".join(sorted(set(symbols)-usable)))
    if len(set(symbols)) > 1:
        from .OptionDesk import BOOK_AGE, stamp
        requested = set(symbols)
        clocks = [stamp({"time": r.get(k, "")}) for r in value["rows"] if r["symbol"] in requested
                  for k in ("source_time", "future_time")]
        if (max(clocks) - min(clocks)).total_seconds() > BOOK_AGE:
            raise ValueError("组合各腿不是同一时间窗口的盘口")
    return value


def guard_option_orders(context, kind, check_frame, allowed):
    """Recheck before strategy orders. Explicit CTA exits and cancellations stay available."""
    from functools import wraps
    methods = ('stra_buy','stra_sell') if kind=='HFT' else (
        'stra_enter_long','stra_enter_short','stra_exit_long','stra_exit_short','stra_set_position'
    )
    def guarded(name, method):
        @wraps(method)
        def submit(stdCode, *args, **kwargs):
            try:
                if stdCode not in allowed:
                    raise ValueError("合约未列入期权运行计划")
                exit_only = name in ('stra_exit_long', 'stra_exit_short') or (
                    name == 'stra_set_position' and (args[0] if args else kwargs.get('qty')) == 0
                )
                if check_frame and not exit_only:
                    frame = check_frame()
                    if kind == 'CTA' and str(context.stra_get_tdate()) != str(frame["trading_day"]):
                        raise ValueError("分析会话与策略交易日不一致")
            except (OSError,ValueError) as exc:
                context.stra_log_text("期权分析门控拒绝委托：" + str(exc))
                return [] if kind=='HFT' else None
            return method(stdCode,*args,**kwargs)
        return submit
    for name in methods:
        wrapper = guarded(name, getattr(context, name))
        setattr(context, name, wrapper)
        alias = name.removeprefix('stra_')
        if hasattr(context, alias):
            setattr(context, alias, wrapper)
