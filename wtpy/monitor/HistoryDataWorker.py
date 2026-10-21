"""Read coverage and merge closed historical bars through WT's native helper."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wtpy.WtCoreDefs import WTSBarStruct, WTSTickStruct
from wtpy.wrapper.WtDtHelper import WtDataHelper


def paths(payload):
    period = payload["period"]
    if period not in ("d", "m1", "m5"):
        raise ValueError("补数周期仅支持 d、m1、m5")
    exchange, ticker = payload["exchange"], payload["ticker"]
    if not all(re.fullmatch(r"[A-Za-z0-9_]+", value) for value in (exchange, ticker)):
        raise ValueError("合约代码无效")
    folder = {"d": "day", "m1": "min1", "m5": "min5"}[period]
    storage = Path(payload["storage"])
    return storage / "his" / folder / exchange / (ticker + ".dsb"), storage / "rt" / folder / exchange / (ticker + ".dmb")


def stamp(bar, period):
    return int(bar.date) if period == "d" else 199000000000 + int(bar.time)


def read_bars(helper, path, historical):
    if not path.exists():
        return []
    reader = helper.read_dsb_bars if historical else helper.read_dmb_bars
    data = reader(str(path))
    if data is None or not len(data):
        if historical:
            raise ValueError("已有历史文件无法读取，已保留原文件")
        return []
    # The native NumPy dtype has the same layout, with turnover/volume aliases.
    return [WTSBarStruct.from_buffer_copy(row.tobytes()) for row in data.ndarray]


def in_range(bar, payload):
    return int(payload["start_date"]) <= int(bar.date) <= int(payload["end_date"])


def coverage(payload, helper):
    historical, realtime = paths(payload)
    records = {}
    for path, is_history, source in ((historical, True, "WT历史"), (realtime, False, "CTP实时")):
        for bar in read_bars(helper, path, is_history):
            if in_range(bar, payload):
                key = stamp(bar, payload["period"])
                records[key] = {"date": int(bar.date), "stamp": key, "source": source}
    return {"records": [records[key] for key in sorted(records)]}


def verify_bars(helper, path, expected, period):
    actual = read_bars(helper, path, True)
    if len(actual) != len(expected):
        raise ValueError("历史文件写入后条数不一致，已保留原文件")
    for before, after in zip(expected, actual):
        for name, _ in WTSBarStruct._fields_:
            left, right = getattr(before, name), getattr(after, name)
            if left != right and not (isinstance(left, float) and math.isnan(left) and math.isnan(right)):
                raise ValueError("历史文件写入后字段不一致，已保留原文件")
    times = [stamp(bar, period) for bar in actual]
    if times != sorted(times):
        raise ValueError("历史文件时间顺序异常，已保留原文件")


def merge(payload, helper):
    historical, realtime = paths(payload)
    period = payload["period"]
    existing = read_bars(helper, historical, True)
    merged = list(existing)
    occupied = {stamp(bar, period) for bar in existing}
    native_copied = 0
    for bar in read_bars(helper, realtime, False):
        key = stamp(bar, period)
        if in_range(bar, payload) and key not in occupied:
            merged.append(bar)
            occupied.add(key)
            native_copied += 1
    imported_dates = set()
    inserted = 0
    fields = {name for name, _ in WTSBarStruct._fields_}
    for item in payload["bars"]:
        bar = WTSBarStruct(**{key: value for key, value in item.items() if key in fields})
        if not in_range(bar, payload):
            raise ValueError("补入数据超出所选交易日")
        key = stamp(bar, period)
        if key not in occupied:
            merged.append(bar)
            occupied.add(key)
            inserted += 1
            imported_dates.add(int(bar.date))
    result = {"rows": inserted, "received_rows": len(payload["bars"]),
              "native_copied_rows": native_copied, "existing_rows": len(existing),
              "total_rows": len(merged), "dates": sorted(imported_dates), "file": str(historical)}
    if not inserted and not native_copied:
        return result
    merged.sort(key=lambda bar: stamp(bar, period))
    historical.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=historical.stem + ".", suffix=".tmp.dsb", dir=historical.parent)
    os.close(handle)
    temporary = Path(name)
    try:
        bars = (WTSBarStruct * len(merged))(*merged)
        if not helper.store_bars(str(temporary), bars, len(merged), period):
            raise ValueError("历史文件写入失败，已保留原文件")
        verify_bars(helper, temporary, merged, period)
        if historical.exists():
            shutil.copy2(historical, historical.with_name(historical.stem + ".previous.dsb"))
        os.replace(temporary, historical)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def run(payload):
    if payload["action"] == "cnequity":
        from cnequity.query import load
        from datetime import datetime
        period = payload["period"]
        if period not in ("d", "m1"):
            raise ValueError("CNEquity 仅接入逐合约日线与 1 分钟线，不提供期货 Tick")
        frame = load("futures_bars" if period == "d" else "futures_minute_bars",
            start=datetime.strptime(str(payload["start_date"]), "%Y%m%d").date(),
            end=datetime.strptime(str(payload["end_date"]), "%Y%m%d").date(),
            symbols=[payload["symbol"]], data_root=payload["data_root"])
        rows = []
        for item in frame.to_dicts():
            if any(item.get(key) is None for key in ("open", "high", "low", "close")):
                continue  # A no-trade exchange row is not a tradable OHLC bar.
            row = {key:item.get(key, 0) for key in ("open", "high", "low", "close", "settle", "amount")}
            row.update(vol=item.get("volume", 0), oi=item.get("open_interest", 0), _money_scale=1)
            if period == "d":
                row["trade_date"] = str(item["trade_date"]).replace("-", "")[:8]
            else:
                row["trade_time"] = str(item["bar_time"])
            rows.append(row)
        return {"rows": rows}
    if payload["action"] == "inspect_many":
        helper = WtDataHelper()
        results = {}
        for item in payload["entries"]:
            try:
                results[item["id"]] = inspect_or_export(dict(item, action="inspect"), helper)
            except (ValueError, OSError) as error:
                results[item["id"]] = dict(rows=0, start=None, end=None, dates=[], error=str(error))
        return {"entries": results}
    if payload["action"] == "akshare":
        import akshare as ak
        symbol, period = payload["ticker"].upper(), payload["period"]
        frame = ak.futures_zh_daily_sina(symbol=symbol) if period == "d" else ak.futures_zh_minute_sina(symbol=symbol, period="1" if period == "m1" else "5")
        rows = []
        for item in frame.to_dict("records"):
            row = {key: item.get(key, 0) for key in ("open", "high", "low", "close")}
            row.update(vol=item.get("volume", 0), oi=item.get("hold", 0))
            if period == "d":
                row["trade_date"] = str(item["date"]).replace("-", "")[:8]
            else:
                row["trade_time"] = str(item["datetime"])
            rows.append(row)
        return {"rows": rows}
    if payload["action"] in ("inspect", "export"):
        return inspect_or_export(payload, WtDataHelper())
    if int(payload["start_date"]) > int(payload["end_date"]):
        raise ValueError("开始交易日不能晚于结束交易日")
    actions = {"coverage": coverage, "merge": merge}
    action = actions.get(payload["action"])
    if action is None:
        raise ValueError("未知的行情补数操作")
    return action(payload, WtDataHelper())


def dataset_records(payload, helper):
    """Snapshot server-selected files. Prefer settled history over overlapping RT days."""
    ticks = payload["period"] == "tick"
    historical_days = set()
    historical = []
    realtime = []
    for filename in payload["files"]:
        path = Path(filename)
        reader = (helper.read_dsb_ticks if path.suffix == ".dsb" else helper.read_dmb_ticks) if ticks else (helper.read_dsb_bars if path.suffix == ".dsb" else helper.read_dmb_bars)
        data = reader(str(path))
        if data is None:
            raise ValueError("行情文件无法读取：" + path.name)
        struct = WTSTickStruct if ticks else WTSBarStruct
        rows = [struct.from_buffer_copy(row.tobytes()) for row in data.ndarray]
        if path.suffix == ".dsb":
            historical.extend(rows)
            if ticks:
                historical_days.update(int(row.trading_date) for row in rows)
        else:
            if not ticks and rows:
                # The live writer may still be updating its final bar. Only
                # historical DSB or earlier realtime bars are closed input.
                rows = rows[:-1]
            realtime.extend(rows)
    if ticks:
        rows = historical + [row for row in realtime if int(row.trading_date) not in historical_days]
        rows.sort(key=lambda row: (int(row.action_date), int(row.action_time)))
    else:
        rows = {stamp(row, payload["period"]): row for row in realtime}
        rows.update({stamp(row, payload["period"]): row for row in historical})
        rows = [rows[key] for key in sorted(rows)]
    start, end = payload.get("start", 0), payload.get("end", 999999999999)
    def event_stamp(row):
        if ticks:
            return int(row.action_date) * 10000 + int(row.action_time) // 100000
        return int(row.date) * 10000 + 1500 if payload["period"] == "d" else stamp(row, payload["period"])
    return [row for row in rows if start <= event_stamp(row) <= end], event_stamp


def inspect_or_export(payload, helper):
    rows, event_stamp = dataset_records(payload, helper)
    result = {"rows": len(rows), "start": min((event_stamp(row) for row in rows), default=None),
              "end": max((event_stamp(row) for row in rows), default=None),
              "dates": sorted({int(row.trading_date if payload["period"] == "tick" else row.date) for row in rows})}
    if payload["action"] != "export":
        return result
    if not rows:
        raise ValueError("所选时间段没有行情，不创建空回测")
    root = Path(payload["destination"])
    exchange, ticker, period = payload["exchange"], payload["ticker"], payload["period"]
    files = []
    if period == "tick":
        for day in result["dates"]:
            values = [row for row in rows if int(row.trading_date) == day]
            path = root / "his/ticks" / exchange / str(day) / (ticker + ".dsb")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not helper.store_ticks(str(path), (WTSTickStruct * len(values))(*values), len(values)):
                raise ValueError("Tick 快照写入失败")
            files.append(str(path))
    else:
        path = root / "his" / {"d":"day", "m1":"min1", "m5":"min5"}[period] / exchange / (ticker + ".dsb")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not helper.store_bars(str(path), (WTSBarStruct * len(rows))(*rows), len(rows), period):
            raise ValueError("K 线快照写入失败")
        files.append(str(path))
    return dict(result, files=files, storage=str(root))


if __name__ == "__main__":
    try:
        result = run(json.load(sys.stdin))
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
