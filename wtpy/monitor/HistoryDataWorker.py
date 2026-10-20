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
from wtpy.WtCoreDefs import WTSBarStruct
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
    if int(payload["start_date"]) > int(payload["end_date"]):
        raise ValueError("开始交易日不能晚于结束交易日")
    actions = {"coverage": coverage, "merge": merge}
    action = actions.get(payload["action"])
    if action is None:
        raise ValueError("未知的行情补数操作")
    return action(payload, WtDataHelper())


if __name__ == "__main__":
    try:
        result = run(json.load(sys.stdin))
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
