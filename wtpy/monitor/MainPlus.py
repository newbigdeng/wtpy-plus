"""Run continuous futures signals through the native Plus executor and recorded market."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from ..ContractCodes import raw_code
from .PlusBacktest import read_json, write_json
from .PlusExecution import effective_execution
from .TradeFinance import effective_finance


def raw_signal_tick(tick, selection, signal):
    row = selection.get(str(tick.trading_date))
    if not row or tick.code.decode() != row["raw"]:
        return None
    result = type(tick).from_buffer_copy(tick)
    result.code = raw_code(signal).encode()
    fields = (
        "price",
        "open",
        "high",
        "low",
        "settle_price",
        "pre_close",
        "pre_settle",
        "upper_limit",
        "lower_limit",
    )
    for field in (
        *fields,
        *(f"{side}_price_{i}" for side in ("ask", "bid") for i in range(10)),
    ):
        value = getattr(result, field)
        if 0 < value < 1e15:
            setattr(result, field, value * row["factor"])
    return result


def record_ticks(source, folder, plan):
    """Freeze only relevant days and keep the outgoing contract on a switch day."""
    from wtpy.wrapper.WtDtHelper import WtDataHelper
    from .HistoryDataWorker import dataset_records
    from wtpy.WtCoreDefs import WTSTickStruct

    helper = WtDataHelper()
    exchange = source["exchange"]
    storage = Path(source["storage"])
    by_day = {}
    previous = None
    for row in plan:
        day = row["day"]
        required = {row["raw"]}
        if previous and previous != row["raw"]:
            required.add(previous)
        files = []
        for raw in sorted(required):
            settled = storage / "his/ticks" / exchange / day / (raw + ".dsb")
            dest = folder / "market-data/his/ticks" / exchange / day / (raw + ".dsb")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if settled.is_file() and not settled.is_symlink():
                shutil.copyfile(settled, dest)
            else:
                realtime = storage / "rt/ticks" / exchange / (raw + ".dmb")
                rows = []
                if realtime.is_file() and not realtime.is_symlink():
                    ticks, _ = dataset_records(
                        dict(period="tick", files=[str(realtime)]), helper
                    )
                    rows = [t for t in ticks if str(t.trading_date) == day]
                if not rows or not helper.store_ticks(
                    str(dest), (WTSTickStruct * len(rows))(*rows), len(rows)
                ):
                    raise ValueError(
                        f"{day} 缺少 {raw} 的真实 Tick；行情接口只提供 K 线"
                    )
            files.append(str(dest))
        by_day[day] = files
        previous = row["raw"]
    return by_day


class DayStream:
    """Validate and release each day's ticks before opening the next day."""

    def __init__(self, files):
        self.files = files
        self.count = 0
        self.first = None
        for rows in self.days():
            if self.first is None:
                self.first = rows[0]
            self.count += len(rows)

    def days(self):
        from .PlusReplayRunner import load_ticks

        for day, files in sorted(self.files.items()):
            rows = load_ticks(dict(kind="native_ticks", files=files))
            present = {bytes(t.code).decode() for t in rows}
            if present != {Path(f).stem for f in files} or any(
                str(t.trading_date) != day or not 0 < t.price < 1e15 for t in rows
            ):
                raise ValueError(f"{day} 的主力或移仓腿 Tick 不完整")
            yield rows

    def __len__(self):
        return self.count

    def __iter__(self):
        for rows in self.days():
            yield from rows

    def __getitem__(self, index):
        if index == 0:
            return self.first
        raise IndexError(index)


def run_plus(folder, task, signal, plan):
    from .HistoryCatalog import dataset_entries

    source = next(
        (
            r
            for r in dataset_entries("/workspace", periods=("tick",))
            if r["id"] == task["dataset"] and r["groupid"] == task["groupid"]
        ),
        None,
    )
    if not source:
        raise ValueError("请选择本组合的真实 Tick 数据集")
    records = record_ticks(source, folder, plan)
    codes = list(dict.fromkeys(r["code"] for r in plan))
    finance = {
        c: effective_finance(
            dict(source, code=c),
            dict(
                fees=dict(
                    open=task["fee"],
                    close=task["fee"],
                    closetoday=task["fee"],
                    byvolume=True,
                    minfee=0,
                ),
                marginrate=task["margin"],
            ),
        )
        for c in codes
    }
    base = folder / "market-base"
    contracts = read_json(base / "contracts.json")
    original = read_json(Path(source["basefiles_map"]["contract"]))
    for c in codes:
        ex = c.split(".")[0]
        raw = raw_code(c)
        if raw not in original.get(ex, {}):
            raise ValueError(f"{c} 缺少冻结的 WT 合约信息")
        contracts[ex][raw] = original[ex][raw]
    write_json(base / "contracts.json", contracts)
    source_base = {
        k: str(base / (n + ".json"))
        for k, n in (
            ("commodity", "commodities"),
            ("contract", "contracts"),
            ("holiday", "holidays"),
            ("session", "sessions"),
            ("hot", "hots"),
        )
    }
    shared = Path(os.environ.get("WT_MAIN_PLUS_ROOT", "/data/plus-tasks")) / task["id"]
    shared.mkdir(mode=0o700)
    # The Plus report service reads frozen real-leg ticks and metadata here.
    shutil.move(str(folder / "market-data"), str(shared / "market-data"))
    relocated = {
        day: [str(shared / Path(f).relative_to(folder)) for f in files]
        for day, files in records.items()
    }
    shutil.copytree(base, shared / "market-base")
    source_base = {
        k: str(shared / "market-base" / Path(f).name) for k, f in source_base.items()
    }
    shutil.move(str(folder / "MyStrategy.py"), str(shared / "MyStrategy.py"))
    start = int(plan[0]["day"] + "0000")
    end = int(plan[-1]["day"] + "2359")
    settings = dict(
        id=task["id"],
        user=task["owner"],
        straid=task["strategy"],
        code=codes[0],
        dataset=source["id"],
        engine_type="CTA",
        params=task.get("params", {}),
        period=task["period"],
        capital=task["capital"],
        finance=finance[codes[0]],
        finance_by_code=finance,
        execution=effective_execution("CTA", start, end),
        risk=None,
        market_source="recorded",
        data_model="recorded_tick",
        mode="plus",
        status="queued",
        progress=0,
        created_at=time.time(),
        updated_at=time.time(),
        start=int(task["replay_start"]),
        end=end,
        processed=0,
        total=0,
        orders=0,
        trades=0,
        market_snapshot=dict(
            code=codes[0],
            codes=codes,
            basefiles=source_base,
            files=[f for files in relocated.values() for f in files],
        ),
        main_plan=plan,
        signal_code=signal,
        days_files=relocated,
    )
    write_json(shared / "task.json", settings)
    task["plus_id"] = task["id"]
    write_json(folder / "task.json", task)
    env = dict(os.environ, WT_PLUS_NATIVE_DIR="/workspace/artifacts/plus-native")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "wtpy.monitor.PlusReplayRunner",
            str(shared),
            "/workspace",
        ],
        cwd=shared,
        env=env,
    )
    final = read_json(shared / "task.json", {})
    final["exitcode"] = proc.returncode
    write_json(shared / "task.json", final)
    if proc.returncode or final.get("status") != "completed":
        raise ValueError(final.get("error") or "Plus 主力 Tick 回放未完成")
    task.update(
        model="真实 Tick / WT 执行器 / TraderMocker",
        calculations=final.get("callbacks", {}).get("on_calculate", 0),
    )
