"""Close the daily signal on the exchange clock while orders stay in the native engine."""

import ctypes
from datetime import datetime, timedelta

from wtpy.WtCoreDefs import WTSBarStruct
from wtpy.WtDataDefs import WtNpKline


class DailyContext:
    def __init__(self, driver, context):
        self.driver = driver
        self.context = context

    def __getattr__(self, name):
        return getattr(self.context, name)

    def stra_get_date(self):
        return (
            self.driver.clock[0] if self.driver.clock else self.context.stra_get_date()
        )

    def stra_get_time(self):
        return (
            self.driver.clock[1] if self.driver.clock else self.context.stra_get_time()
        )

    def stra_get_bars(self, stdCode, period, count, isMain=False):
        if period != "d1":
            if isMain:
                raise ValueError("此任务的主周期为日线")
            return self.context.stra_get_bars(stdCode, period, count, False)
        if stdCode != self.driver.code:
            if isMain:
                raise ValueError("日线主序列须为所选合约")
            return self.context.stra_get_bars(stdCode, period, count, False)
        rows = self.driver.closed[-count:]
        if not rows:
            return None
        data = WtNpKline(isDay=True, forceCopy=True)
        data.set_data(ctypes.pointer((WTSBarStruct * len(rows))(*rows)[0]), len(rows))
        return data

    get_date = stra_get_date
    get_time = stra_get_time
    get_bars = stra_get_bars
    stra_prepare_bars = stra_get_bars
    prepare_bars = stra_get_bars


class DailyReplay:
    """Detect a crossed session close without inventing a later final tick."""

    def __init__(self, checked, code, folder):
        self.strategy = checked
        self.code = code
        from wtpy.ContractCodes import raw_code

        self.raw = raw_code(code).encode()
        self.closed = []
        file = (
            folder
            / "storage/his/day"
            / code.split(".")[0]
            / (self.raw.decode() + ".dsb")
        )
        if file.exists():
            from wtpy.wrapper.WtDtHelper import WtDataHelper

            warm = WtDataHelper().read_dsb_bars(str(file))
            if warm is not None:
                self.closed = [
                    WTSBarStruct.from_buffer_copy(r.tobytes()) for r in warm.ndarray
                ]
        self.last = None
        self.clock = None
        self.view = None

    def name(self):
        return self.strategy.name()

    def on_init(self, context):
        self.view = DailyContext(self, context)
        session = context.stra_get_sessinfo(self.code)
        if not session or not session.sections:
            raise ValueError("无法确定合约日盘收盘时间")
        self.close = session.getCloseTime()
        context.stra_sub_ticks(self.code)
        self.strategy.on_init(self.view)

    def __getattr__(self, name):
        if name in ("on_calculate", "on_calculate_done", "on_bar"):
            return lambda *args: None
        target = getattr(self.strategy, name)
        if name.startswith("on_"):
            return lambda ctx, *args: target(self.view, *args)
        return target

    def observe(self, tick):
        if bytes(tick.code) == self.raw:
            self.last = tick

    def close_if_due(self, date, time):
        t = self.last
        if t is None or not self.view:
            return
        day = t.trading_date
        if date * 10000 + time < day * 10000 + self.close:
            return
        if self.closed and self.closed[-1].date >= day:
            return
        end = datetime.strptime(f"{day:08}{self.close:04}", "%Y%m%d%H%M")
        stamp = datetime.strptime(
            f"{t.action_date:08}{t.action_time // 100000:04}", "%Y%m%d%H%M"
        )
        if stamp < end - timedelta(minutes=1):
            raise ValueError(f"{day} 缺少收市附近的真实 Tick，无法形成完整日线")
        if (
            not 0
            < t.low
            <= min(t.open, t.price)
            <= max(t.open, t.price)
            <= t.high
            < 1e15
        ):
            raise ValueError(f"{day} 缺少有效日开高低收")
        bar = WTSBarStruct(
            date=day,
            open=t.open,
            high=t.high,
            low=t.low,
            close=t.price,
            vol=t.total_volume,
            money=t.total_turnover,
            hold=t.open_interest,
        )
        self.closed.append(bar)
        self.clock = (day, self.close)
        self.strategy.replay_tick_time = day * 1000000000 + self.close * 100000
        try:
            self.strategy.on_bar(
                self.view,
                self.code,
                "d1",
                dict(
                    date=day,
                    time=day,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    vol=bar.vol,
                ),
            )
            self.strategy.on_calculate(self.view)
            self.strategy.on_calculate_done(self.view)
        finally:
            self.clock = None
