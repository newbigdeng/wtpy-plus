"""Lake-first futures history; CNEquity runs in its own Python environment."""
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def settings(private):
    value = private.get("cnequity", {})
    return {key: str(value.get(key) or "") for key in ("python", "config", "data_root")}


def availability(value):
    if not value["data_root"] or not value["config"]:
        return dict(available=False, state="not_configured", reason="尚未配置 CNEquity 数据湖与采集配置")
    python = value["python"] or sys.executable
    if not Path(python).is_file() or not Path(value["config"]).is_file():
        return dict(available=False, state="not_connected", reason="CNEquity 研究环境或配置文件不存在")
    if python == sys.executable and importlib.util.find_spec("cnequity") is None:
        return dict(available=False, state="not_connected", reason="当前 Python 环境未安装 CNEquity，请选择独立研究环境")
    return dict(available=True, state="ready", reason="先查数据湖，缺失时采集可获取的行情，再补入 WT；数据覆盖需按合约与日期检查")


def request(value, payload, timeout=30):
    env = dict(os.environ, POLARS_MAX_THREADS="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", CNE_LANG="zh_CN")
    try:
        process = subprocess.run([value["python"] or sys.executable, str(Path(__file__).resolve())],
            input=json.dumps(dict(payload, config=value["config"], data_root=value["data_root"])),
            capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("CNEquity 独立读取进程未完成，请检查研究环境与任务日志") from error
    try:
        result = json.loads(process.stdout.splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError("CNEquity 未返回有效结果，WT 数据未修改") from None
    if process.returncode or result.get("error"):
        raise ValueError(result.get("error") or "CNEquity 读取失败")
    return result


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def row_stamp(row, period):
    key = "trade_date" if period == "d" else "bar_time"
    value = row[key]
    return int(value.strftime("%Y%m%d" if period == "d" else "%Y%m%d%H%M"))


def resample_five(rows, endings, trading_days):
    """Require all five closed minutes, including across midnight."""
    minutes = {row_stamp(row, "m1"): row for row in rows}
    result = []
    for stamp in sorted(endings):
        end = datetime.strptime(str(stamp), "%Y%m%d%H%M")
        keys = [int((end-timedelta(minutes=n)).strftime("%Y%m%d%H%M")) for n in range(4, -1, -1)]
        if not all(key in minutes for key in keys):
            continue
        bars = [minutes[key] for key in keys]
        result.append(dict(bars[-1], bar_time=end, trade_date=trading_days[stamp], frequency="5m",
            open=bars[0]["open"], high=max(row["high"] for row in bars), low=min(row["low"] for row in bars),
            close=bars[-1]["close"], volume=sum(row.get("volume") or 0 for row in bars),
            amount=sum(row.get("amount") or 0 for row in bars), source="derived_sina_1m"))
    return result


def usable(rows):
    return [row for row in rows if all(row.get(key) is not None for key in ("open", "high", "low", "close"))]


def custom_five_path(payload):
    return Path(payload["data_root"])/"derived/wt_futures_minute_5m"/(payload["symbol"]+".parquet")


def read_lake(payload, cfg):
    from cnequity.query import load
    period = payload["period"]
    start = datetime.strptime(str(payload["start_date"]), "%Y%m%d").date()
    end = datetime.strptime(str(payload["end_date"]), "%Y%m%d").date()
    dataset = "futures_bars" if period == "d" else "futures_minute_bars"
    # Include the preceding session's night; WT supplies the trading-day map.
    files = Path(payload["data_root"])/"curated"/dataset
    if next(files.rglob("*.parquet"), None) is None:
        rows = []
    else:
        frame = load(dataset, start=start-timedelta(days=7) if period != "d" else start,
                     end=end, symbols=[payload["symbol"]], config=cfg)
        rows = usable(frame.to_dicts())
    expected = {int(day): set(stamps) for day, stamps in payload["expected"].items()}
    stamps = set().union(*expected.values()) if expected else set()
    trading_days = {stamp: datetime.strptime(str(day), "%Y%m%d").date()
                    for day, values in expected.items() for stamp in values}
    if period == "m5":
        rows = resample_five(rows, stamps, trading_days)
    selected = {row_stamp(row, period): row for row in rows if row_stamp(row, period) in stamps}
    dates = [dict(date=day, rows=sum(stamp in selected for stamp in wanted), expected_rows=len(wanted),
                  status="closed" if not wanted else "present" if wanted.issubset(selected) else "partial" if wanted.intersection(selected) else "missing")
             for day, wanted in sorted(expected.items())]
    ordered = [selected[stamp] for stamp in sorted(selected)]
    return ordered, dict(rows=len(ordered), dates=dates, missing_dates=[row["date"] for row in dates if row["status"] in ("missing", "partial")],
        start=min(selected, default=None), end=max(selected, default=None), sources=sorted({row.get("source", "CNEquity") for row in ordered}))


def save_five(payload, rows):
    if not rows:
        return
    import polars as pl
    path = custom_five_path(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = pl.read_parquet(path) if path.is_file() else pl.DataFrame()
    new = pl.DataFrame(rows, infer_schema_length=None)
    combined = pl.concat([old, new], how="diagonal_relaxed").unique(subset=["symbol", "bar_time"], keep="last").sort("bar_time")
    temporary = path.with_suffix(".tmp.parquet")
    combined.write_parquet(temporary)
    os.replace(temporary, path)


def collect(payload, cfg):
    """CNEquity owns source fetching, normalization and publication."""
    cne = str(Path(sys.executable).with_name("cne"))
    dataset = "futures_bars" if payload["period"] == "d" else "futures_minute_bars"
    args = [cne, "backfill", dataset, "--config", payload["config"]]
    if dataset == "futures_bars":
        args += ["--exchange", payload["symbol"].split(".")[1].lower(),
                 "--start", str(min(payload["missing_dates"])), "--end", str(max(payload["missing_dates"]))]
        # CNEquity's daily CLI accepts ISO dates only.
        args[-3] = datetime.strptime(args[-3], "%Y%m%d").strftime("%Y-%m-%d")
        args[-1] = datetime.strptime(args[-1], "%Y%m%d").strftime("%Y-%m-%d")
    else:
        args += ["--symbols", payload["symbol"]]
    log = Path(payload["data_root"])/"logs"/("wt-"+dataset+"-"+payload["symbol"]+".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as stream:
        process = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=750)
    warnings = [] if not process.returncode else ["CNEquity 采集未全部完成，请查看数据湖任务日志"]
    return warnings


def run(payload):
    import fcntl
    from cnequity.config import load_config
    cfg = load_config(payload["config"])
    if str(Path(cfg.data_root).resolve()) != str(Path(payload["data_root"]).resolve()):
        raise ValueError("CNEquity 配置与数据湖目录不一致")
    rows, summary = read_lake(payload, cfg)
    before = summary["rows"]
    attempted, warnings = False, []
    if payload["action"] == "ensure" and summary["missing_dates"]:
        state = Path(payload["data_root"])/"meta/wt-history"
        state.mkdir(parents=True, exist_ok=True)
        with (state/"fetch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows, summary = read_lake(payload, cfg)
            attempts_path = state/"attempts.json"
            attempts = json.loads(attempts_path.read_text()) if attempts_path.is_file() else {}
            cache_period = "m1" if payload["period"] == "m5" else payload["period"]
            key = "|".join(map(str, (payload["symbol"], cache_period, payload["start_date"], payload["end_date"])))
            if summary["missing_dates"] and time.time()-attempts.get(key, 0) > 900:
                warnings = collect(dict(payload, missing_dates=summary["missing_dates"]), cfg)
                attempted = True
                if not warnings:
                    attempts[key] = time.time()
                    atomic_json(attempts_path, attempts)
            rows, summary = read_lake(payload, cfg)
    if payload["action"] == "ensure" and payload["period"] == "m5":
        save_five(payload, rows)
    output = []
    for row in rows:
        result = {key: row.get(key) or 0 for key in ("open", "high", "low", "close", "settle", "amount")}
        result.update(vol=row.get("volume") or 0, oi=row.get("open_interest") or 0, _money_scale=1)
        result["trade_date" if payload["period"] == "d" else "trade_time"] = str(row["trade_date"] if payload["period"] == "d" else row["bar_time"])
        if payload["period"] == "d":
            result["trade_date"] = result["trade_date"].replace("-", "")
        output.append(result)
    summary.update(before_rows=before, download_attempted=attempted, warnings=warnings,
                   message="五分钟由五根完整有效的一分钟线聚合；只返回实际存在的行情" if payload["period"] == "m5" else "已查询数据湖；只返回实际存在的行情，分钟源仅提供最近窗口")
    return dict(rows=output, lake=summary)


if __name__ == "__main__":
    try:
        answer = run(json.load(sys.stdin))
    except Exception as error:
        print(json.dumps(dict(error=str(error)), ensure_ascii=False), flush=True)
        raise SystemExit(1)
    print(json.dumps(answer, ensure_ascii=False, allow_nan=False), flush=True)
