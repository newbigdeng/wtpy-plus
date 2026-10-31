"""Small, derived views of native replay records; full report files stay intact."""
import csv
from itertools import islice
import math
from pathlib import Path

LEDGERS = ("orders", "trades", "funds", "positions", "events", "rejections", "risk")
PAGE_SIZE = 200
CURVE_POINTS = 600


def native_performance(summary, funds):
    """Derive display metrics from native equity, without another account ledger."""
    capital, equity = summary.get("capital"), summary.get("equity")
    if (summary.get("account_source") != "TraderMocker原生账户"
            or not isinstance(capital, (int, float)) or not math.isfinite(capital) or capital <= 0
            or not isinstance(equity, (int, float)) or not math.isfinite(equity)):
        return None
    peak, drawdown, samples, days = capital, 0.0, 0, set()
    for row in funds:
        value = row.get("equity")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        peak = max(peak, value)
        drawdown = max(drawdown, (peak-value)/peak*100)
        samples += 1
        if row.get("trading_date"):
            days.add(row["trading_date"])
    if not samples:
        return None
    return {"total_return": (equity/capital-1)*100, "max_falldown": drawdown,
            "annual_return": None, "days": len(days), "capital": capital, "equity": equity,
            "actual_trades": summary.get("actual_trades"), "account_source": summary["account_source"]}


def fund_row(original):
    row = {key:int(value) if key in ("date", "time", "trading_date") else float(value)
           for key,value in original.items()}
    if any(isinstance(value,float) and not math.isfinite(value) for value in row.values()):
        raise ValueError("原生资金账簿包含无效数值")
    row.update(fee=row["commission"], profit=row["realized"], dynprofit=row["unrealized"])
    return row


def curve_label(row, index):
    date = str(row.get("date", ""))
    clock = str(row.get("time", "")).zfill(9)
    if len(date) == 8 and len(clock) == 9:
        return f"{date[:4]}-{date[4:6]}-{date[6:]} {clock[:2]}:{clock[2:4]}:{clock[4:6]}.{clock[6:]}"
    return str(row.get("timestamp", index))


def equity_curve(funds):
    """Preserve first/last and each bucket's low/high; no account recalculation."""
    if len(funds) <= CURVE_POINTS:
        indices = range(len(funds))
    else:
        selected = {0, len(funds)-1}
        buckets = (CURVE_POINTS-2)//2
        for bucket in range(buckets):
            start = 1+(len(funds)-2)*bucket//buckets
            end = 1+(len(funds)-2)*(bucket+1)//buckets
            window = range(start, end)
            selected.add(min(window, key=lambda i:funds[i].get("equity", funds[i].get("balance", 0))))
            selected.add(max(window, key=lambda i:funds[i].get("equity", funds[i].get("balance", 0))))
        indices = sorted(selected)
    return [[curve_label(funds[i], i), funds[i].get("equity", funds[i].get("balance"))]
            for i in indices]


def save_view(folder, report, cache_folder=None, source_stamp=None):
    from .PlusBacktest import write_json
    folder = Path(folder)
    source = source_stamp or (folder/"report.json").stat()
    view = {key:value for key,value in report.items() if key not in LEDGERS}
    rows = {key:report.get(key, []) for key in LEDGERS if key != "risk"}
    rows["risk"] = report.get("risk", {}).get("events", [])
    view.update({key:values[:PAGE_SIZE] for key,values in rows.items() if key != "risk"})
    view["risk"] = {**report.get("risk", {}), "events":rows["risk"][:PAGE_SIZE]}
    view["ledger_totals"] = {key:len(values) for key,values in rows.items()}
    view["equity_curve"] = equity_curve(rows["funds"])
    view["performance"] = native_performance(report.get("summary", {}), rows["funds"])
    view["_format"] = 1
    if not view.get("diagnostics") and not rows["orders"]:
        view["diagnostics"] = {"message":"没有委托记录。旧任务缺少逐阶段诊断，可保留原记录并用新版回放。"}
    view["_source"] = [source.st_size, source.st_mtime_ns]
    cache_folder = Path(cache_folder) if cache_folder else folder
    cache_folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(cache_folder/"report-view.json", view)
    return view


def read_view(folder, cache_folder=None):
    from .PlusBacktest import read_json
    folder = Path(folder)
    source = folder/"report.json"
    if not source.is_file():
        return {}
    stamp = source.stat()
    cache_folder = Path(cache_folder) if cache_folder else folder
    view = read_json(cache_folder/"report-view.json", {}) or {}
    source_marker = [stamp.st_size, stamp.st_mtime_ns]
    cache_dirty = False
    if (cache_folder != folder
            and (view.get("_format") != 1 or view.get("_source") != source_marker)):
        view = read_json(folder/"report-view.json", {}) or {}
        cache_dirty = view.get("_format") == 1 and view.get("_source") == source_marker
    if view.get("_format") != 1 or view.get("_source") != source_marker:
        report = read_json(source, {}) or {}
        view = save_view(folder, report, cache_folder, stamp)
    elif "performance" not in view:
        # Existing reports can be large. Upgrade only the small cached view,
        # streaming the complete native CSV rather than its first display page.
        funds = folder/"replay_funds.csv"
        if funds.is_file():
            with funds.open(encoding="utf-8", newline="") as stream:
                view["performance"] = native_performance(view.get("summary", {}), map(fund_row, csv.DictReader(stream)))
        else:
            view["performance"] = None
        cache_dirty = True
    if cache_dirty:
        from .PlusBacktest import write_json
        cache_folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_json(cache_folder/"report-view.json", view)
    return view


def ledger_page(folder, view, ledger, offset, limit):
    from .PlusBacktest import read_json
    if ledger not in LEDGERS:
        raise ValueError("未知回测账簿")
    if ledger == "funds" and (Path(folder)/"replay_funds.csv").is_file():
        with (Path(folder)/"replay_funds.csv").open(encoding="utf-8", newline="") as stream:
            rows = [fund_row(row) for row in islice(csv.DictReader(stream), offset, offset+limit)]
    elif offset+limit <= PAGE_SIZE:
        values = view.get("risk", {}).get("events", []) if ledger == "risk" else view.get(ledger, [])
        rows = values[offset:offset+limit]
    else:
        report = read_json(Path(folder)/"report.json", {}) or {}
        values = report.get("risk", {}).get("events", []) if ledger == "risk" else report.get(ledger, [])
        rows = values[offset:offset+limit]
    return {"id":ledger, "offset":offset, "limit":limit,
            "total":view.get("ledger_totals", {}).get(ledger, 0), "rows":rows}


def log_tail(folder, limit=12000):
    folder = Path(folder)
    files = [folder/"runner.log"]+sorted(folder.glob("native*.log"))
    parts = []
    for path in files:
        if not path.is_file() or not path.stat().st_size:
            continue
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size-limit))
            parts.append(path.name+"\n"+stream.read(limit).decode("utf-8", errors="replace"))
    return "\n".join(parts)[-limit:]
