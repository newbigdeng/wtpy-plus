"""Freeze the real option books for causal fills and a separate signal-only tick stream."""

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from wtpy.WtCoreDefs import WTSTickStruct
from wtpy.wrapper.WtDtHelper import WtDataHelper
from .MainPlus import record_ticks, raw_signal_tick
from ..ContractCodes import raw_code


def option_ticks(root, folder, task, signal, plan):
    from .AssetWorkspace import asset_catalog
    from .HistoryDataWorker import dataset_records

    source = next(
        (
            r
            for r in asset_catalog(root, "option")
            if r["id"] == task["dataset"] and r["period"] == "tick"
        ),
        None,
    )
    if not source:
        raise ValueError("请选择期权工作区的真实 Tick 数据集")
    if source["exchange"] != task["product"].partition(".")[0]:
        raise ValueError("期权 Tick 必须来自所选交易所")
    files = record_ticks(source, folder, plan)
    selection = {r["day"]: r for r in plan}
    minutes = defaultdict(dict)
    helper = WtDataHelper()
    first = None
    for day, paths in files.items():
        for path in paths:
            ticks, _ = dataset_records(dict(period="tick", files=[path]), helper)
            signal_ticks = []
            raw = Path(path).stem
            if not ticks or any(
                str(t.trading_date) != day or bytes(t.code).decode() != raw
                for t in ticks
            ):
                raise ValueError(f"{day} {raw} 的 Tick 与合约或交易日不符")
            for t in ticks:
                if not 0 < t.price < 1e15:
                    continue
                stamp = datetime.strptime(
                    f"{t.action_date:08}{t.action_time:09}"[:14], "%Y%m%d%H%M%S"
                )
                if first is None or stamp < first:
                    first = stamp
                if t.volume > 0:
                    close = stamp.replace(second=0) + (
                        timedelta(minutes=1)
                        if stamp.second or t.action_time % 1000
                        else timedelta()
                    )
                    key = close.strftime("%Y%m%d%H%M")
                    bar = minutes[raw].setdefault(
                        key,
                        dict(
                            day=day,
                            open=t.price,
                            high=t.price,
                            low=t.price,
                            close=t.price,
                            volume=0,
                        ),
                    )
                    bar.update(
                        high=max(bar["high"], t.price),
                        low=min(bar["low"], t.price),
                        close=t.price,
                        volume=bar["volume"] + t.volume,
                    )
                clone = raw_signal_tick(t, selection, signal)
                if clone is not None:
                    signal_ticks.append(clone)
            if selection[day]["raw"] == raw:
                dest = Path(path).with_name(raw_code(signal) + ".dsb")
                if not signal_ticks or not helper.store_ticks(
                    str(dest),
                    (WTSTickStruct * len(signal_ticks))(*signal_ticks),
                    len(signal_ticks),
                ):
                    raise ValueError(f"{day} 缺少连续信号的真实盘口")
    if first is None:
        raise ValueError("没有可用于撮合的期权 Tick")
    return minutes, int((first - timedelta(minutes=1)).strftime("%Y%m%d%H%M"))
