"""Route a continuous CTA signal to actual WT contract positions."""


class RoutedContext:
    def __init__(self, bridge, ctx):
        self.bridge = bridge
        self.ctx = ctx

    def __getattr__(self, name):
        base = self.ctx
        if not name.startswith("stra_") and hasattr(type(self), "stra_" + name):
            return getattr(self, "stra_" + name)
        if name in ("stra_get_ticks", "get_ticks", "stra_sub_ticks", "sub_ticks"):
            raise ValueError("逐 K 回放没有真实 Tick")
        return getattr(base, name)

    def stra_get_position(self, stdCode, bonlyvalid=False, usertag=""):
        self.bridge.check(stdCode)
        return sum(
            self.ctx.stra_get_position(c, bonlyvalid, usertag)
            for c in self.bridge.held_codes
        )

    def stra_get_rawcode(self, stdCode):
        self.bridge.check(stdCode)
        return self.ctx.stra_get_rawcode(self.bridge.active)

    def stra_get_day_price(self, stdCode, flag=0):
        self.bridge.check(stdCode)
        if flag:
            raise ValueError("连续 CTA 的盘中日高低收请从已闭合 K 线读取")
        return self.ctx.stra_get_day_price(stdCode, flag)

    def stra_get_position_avgpx(self, stdCode):
        self.bridge.check(stdCode)
        return self.ctx.stra_get_position_avgpx(self.bridge.active) * self.bridge.factor

    def stra_get_position_profit(self, stdCode):
        self.bridge.check(stdCode)
        return sum(self.ctx.stra_get_position_profit(c) for c in self.bridge.held_codes)

    def stra_set_position(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        self.bridge.check(stdCode)
        if self.bridge.previous and (limitprice or stopprice):
            raise ValueError("移仓未完成时不能跨合约转发条件委托")
        self.bridge.target = qty
        if not self.bridge.previous:
            self.ctx.stra_set_position(
                self.bridge.active,
                qty,
                usertag,
                limitprice / self.bridge.factor,
                stopprice / self.bridge.factor,
            )

    def stra_enter_long(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        self.stra_set_position(
            stdCode,
            max(0, self.stra_get_position(stdCode)) + qty,
            usertag,
            limitprice,
            stopprice,
        )

    def stra_enter_short(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        self.stra_set_position(
            stdCode,
            min(0, self.stra_get_position(stdCode)) - qty,
            usertag,
            limitprice,
            stopprice,
        )

    def stra_exit_long(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        pos = self.stra_get_position(stdCode)
        if pos > 0:
            self.stra_set_position(
                stdCode, max(0, pos - qty), usertag, limitprice, stopprice
            )

    def stra_exit_short(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        pos = self.stra_get_position(stdCode)
        if pos < 0:
            self.stra_set_position(
                stdCode, min(0, pos + qty), usertag, limitprice, stopprice
            )


class ContinuousStrategy:
    def __init__(self, strategy, signal, plan, period):
        self.strategy = strategy
        self.signal = signal
        self.period = period
        self.plan = {r["day"]: r for r in plan}
        self.active = plan[0]["code"]
        self.factor = plan[0]["factor"]
        self.subscribe_ticks = False
        self.released = None
        self.held_codes = {self.active}
        self.previous = None
        self.target = 0
        self.tag = ""

    def name(self):
        return self.strategy.name()

    def check(self, code):
        if code != self.signal:
            raise ValueError("连续 CTA 只能通过策略的 code 参数设置目标仓位")

    def on_init(self, ctx):
        self.view = RoutedContext(self, ctx)
        ctx.stra_prepare_bars(self.signal, self.period, 1, True)
        for code in dict.fromkeys(r["code"] for r in self.plan.values()):
            ctx.stra_prepare_bars(code, self.period, 1, False)
            if self.released or self.subscribe_ticks:
                ctx.stra_sub_ticks(code)
        self.strategy.on_init(self.view)

    def on_session_begin(self, ctx, day):
        row = self.plan.get(str(day))
        if row:
            if row["code"] != self.active:
                if self.previous:
                    raise ValueError("上一次移仓尚未完成")
                self.previous = self.active
                self.active = row["code"]
                self.held_codes.add(self.active)
                self.tag = "roll:" + str(day)
                ctx.stra_set_position(self.previous, 0, self.tag)
            self.factor = row["factor"]
        self.strategy.on_session_begin(self.view, day)

    def on_calculate(self, ctx):
        if (
            self.previous
            and not ctx.stra_get_position(self.previous)
            and (not self.released or self.released(ctx, self.previous))
        ):
            self.previous = None
            if self.target:
                ctx.stra_set_position(self.active, self.target, self.tag)
        if self.previous:
            return
        self.strategy.on_calculate(self.view)

    def on_bar(self, ctx, code, period, bar):
        if code == self.signal and not self.previous:
            self.strategy.on_bar(self.view, code, period, bar)

    def on_session_end(self, ctx, day):
        self.strategy.on_session_end(self.view, day)

    def on_calculate_done(self, ctx):
        self.strategy.on_calculate_done(self.view)

    def on_backtest_end(self, ctx):
        if self.previous:
            raise ValueError("数据在旧仓平完前结束")
        self.strategy.on_backtest_end(self.view)

    def on_tick(self, *args):
        pass

    def on_condition_triggered(self, ctx, code, target, price, tag):
        self.strategy.on_condition_triggered(
            self.view, self.signal, target, price * self.factor, tag
        )
