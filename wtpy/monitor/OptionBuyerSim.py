"""One-lot buyer diagnostic. The exchange ledger, not a local fill model, is authoritative."""

import argparse, fcntl, json, os, time
from pathlib import Path
from queue import Empty
from threading import Event
from openctp_ctp import thostmduserapi as md
from .OptionSimCounter import Desk, field, td
from .AssetMarkets import instrument

TERMINAL = {"0", "2", "4", "5"}


def snapshot(td):
    positions = [r for r in td.request("QryInvestorPosition", **td.who) if r["Position"]]
    orders = td.request("QryOrder", **td.who)
    acct = next(r for r in td.request("QryTradingAccount", **td.who) if r["CurrencyID"] == "CNY")
    return dict(
        positions=positions, active=[r for r in orders if r["OrderStatus"] not in TERMINAL], account=acct
    )


class Quote(md.CThostFtdcMdSpi):
    def __init__(self, symbol):
        super().__init__()
        self.symbol = symbol
        self.latest = None
        self.ready = Event()
        self.api = md.CThostFtdcMdApi.CreateFtdcMdApi()
        self.api.RegisterSpi(self)
        self.api.RegisterFront("tcp://182.254.243.31:40011")
        self.api.Init()

    def OnFrontConnected(self):
        self.api.ReqUserLogin(md.CThostFtdcReqUserLoginField(), 1)

    def OnRspUserLogin(self, r, i, n, last):
        if not i or not i.ErrorID:
            self.api.SubscribeMarketData([self.symbol.encode()], 1)

    def OnFrontDisconnected(self, reason):
        self.latest = None
        self.ready.clear()

    def OnRtnDepthMarketData(self, r):
        if (
            r.InstrumentID == self.symbol
            and 0 < r.LowerLimitPrice <= r.BidPrice1 <= r.AskPrice1 <= r.UpperLimitPrice < 1e7
            and min(r.BidVolume1, r.AskVolume1) > 0
        ):
            self.latest = (
                time.monotonic(),
                r.TradingDay,
                r.BidPrice1,
                r.AskPrice1,
                r.LowerLimitPrice,
                r.UpperLimitPrice,
                r.UpdateTime,
            )
            self.ready.set()
        else:
            self.latest = None
            self.ready.clear()

    def book(self, day):
        if not self.ready.wait(18) or not self.latest:
            raise ValueError("无双边盘口")
        stamp, trade_day, *book = self.latest
        if trade_day != day or time.monotonic() - stamp > 45:
            raise ValueError("无本仿真交易日的新鲜盘口")
        return book


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("symbol", help="一份大商所豆粕期权")
    p.add_argument("--profile", required=True, help="SimNow 7×24 交易配置")
    p.add_argument("--root", type=Path, required=True, help="期权资产目录")
    p.add_argument("--premium-limit", type=float, help="一手买入最多支付的权利金")
    actions = p.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true", help="实际发送仿真撤单、买开和卖平")
    actions.add_argument("--audit", action="store_true", help="同交易日重新向柜台对账，不发委托")
    args = p.parse_args()
    ex, symbol = instrument("option", args.symbol)
    import re

    if (
        ex != "DCE"
        or not re.fullmatch(r"m[0-9]{4}-[CP]-[0-9]+", symbol)
        or (args.apply and not 0 < (args.premium_limit or 0) < 1e8)
    ):
        p.error("仅支持大商所豆粕商品期权，权利金上限必须为有限正数")
    if not args.apply and not args.audit:
        print("预览：仅一手；先撤买单，再买开并卖平。")
        return
    root = args.root / "lifecycle"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    with (root / "counter.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args, symbol, root)


def run(args, SYMBOL, root):
    desk = Desk(args.profile)
    try:
        desk.connect()
        journal = root / ("buyer-" + desk.login["TradingDay"] + "-" + SYMBOL + ".jsonl")
        if args.audit:
            audit(desk, journal, SYMBOL)
            return
        with journal.open("x", encoding="utf8") as out:
            execute(desk, SYMBOL, out, args.premium_limit)
    finally:
        desk.close()


def audit(desk, journal, symbol):
    from .OptionLedger import reconcile

    rows = {r["kind"]: r for r in map(json.loads, journal.read_text(encoding="utf8").splitlines())}
    if not {"before", "opened", "final", "broker"} <= rows.keys():
        raise ValueError("任务未完成；请核查柜台持仓和活动委托")
    keys = {(r["FrontID"], r["SessionID"], r["OrderRef"], r["OrderSysID"]) for r in rows["broker"]["orders"]}
    orders = [
        r
        for r in desk.request("QryOrder", **desk.who, InstrumentID=symbol)
        if (r["FrontID"], r["SessionID"], r["OrderRef"], r["OrderSysID"]) in keys
    ]
    ids = {r["OrderSysID"] for r in orders}
    fills = [r for r in desk.request("QryTrade", **desk.who, InstrumentID=symbol) if r["OrderSysID"] in ids]
    result = reconcile(rows["before"], rows["opened"], rows["final"], orders, fills, rows["before"]["mult"])
    now = snapshot(desk)
    result.update(
        current_positions=sum(p["Position"] for p in now["positions"]), current_working=len(now["active"])
    )
    print(json.dumps(result, ensure_ascii=False))
    if not result["matched"]:
        raise RuntimeError("委托、成交或资金未对上")


def execute(desk, SYMBOL, out, limit):
    mdspi = None

    def log(kind, **v):
        out.write(json.dumps(dict(kind=kind, **v), ensure_ascii=False) + "\n")
        out.flush()
        os.fsync(out.fileno())

    try:
        day = desk.login["TradingDay"]
        (i,) = desk.request("QryInstrument", ExchangeID="DCE", InstrumentID=SYMBOL)
        if not (
            i["IsTrading"]
            and i["VolumeMultiple"] == 10
            and i["UnderlyingInstrID"] == SYMBOL.split("-")[0]
            and i["ExpireDate"] > day
        ):
            raise ValueError("合约、乘数或到期日不符合豆粕试单条件")
        initial = snapshot(desk)
        log("before", **initial, day=day, mult=i["VolumeMultiple"])
        if initial["active"] or initial["positions"]:
            raise ValueError("账户须空仓且无活动委托")
        if not any(x["ConfirmDate"] == day for x in desk.request("QrySettlementInfoConfirm", **desk.who)):
            desk.request("SettlementInfoConfirm", **desk.who)
        mdspi = Quote(SYMBOL)
        bid, ask, low, high, stamp = mdspi.book(day)
        if ask * 10 > limit or initial["account"]["Available"] <= ask * 10 + 100:
            raise ValueError("权利金或资金超限")
        ref = int(desk.login["MaxOrderRef"].strip() or "0")
        statuses = {}
        mine = set()

        def notice(kind, row):
            if (
                kind == "order"
                and row["FrontID"] == desk.login["FrontID"]
                and row["SessionID"] == desk.login["SessionID"]
                and row["OrderRef"].strip() in mine
            ):
                statuses[row["OrderRef"].strip()] = row
                log(kind, row=row)
            elif kind == "trade" and row["InstrumentID"] == SYMBOL:
                log(kind, row=row)

        desk.on_notification = notice

        def until(ref, waiting, seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                row = statuses.get(ref)
                if row and row["OrderStatus"] in waiting:
                    return row
                try:
                    desk.take(min(1, end - time.monotonic()))
                except Empty:
                    pass
            return None

        def place(side, offset, limit):
            nonlocal ref
            ref += 1
            r = str(ref)
            mine.add(r)
            p = field(
                "InputOrder",
                **desk.who,
                UserID=desk.profile["user"],
                ExchangeID="DCE",
                InstrumentID=SYMBOL,
                OrderRef=r,
                OrderPriceType=td.THOST_FTDC_OPT_LimitPrice,
                Direction=side,
                CombOffsetFlag=offset,
                CombHedgeFlag=td.THOST_FTDC_HF_Speculation,
                LimitPrice=limit,
                VolumeTotalOriginal=1,
                TimeCondition=td.THOST_FTDC_TC_GFD,
                VolumeCondition=td.THOST_FTDC_VC_AV,
                MinVolume=1,
                ContingentCondition=td.THOST_FTDC_CC_Immediately,
                ForceCloseReason=td.THOST_FTDC_FCC_NotForceClose,
            )
            desk.id += 1
            log("submit", ref=r, limit=limit, side=side, offset=offset, volume=1)
            rc = desk.api.ReqOrderInsert(p, desk.id)
            if rc:
                raise RuntimeError(("insert", rc))
            return r

        def cancel(r):
            p = field(
                "InputOrderAction",
                **desk.who,
                UserID=desk.profile["user"],
                ExchangeID="DCE",
                InstrumentID=SYMBOL,
                OrderRef=r,
                FrontID=desk.login["FrontID"],
                SessionID=desk.login["SessionID"],
                ActionFlag=td.THOST_FTDC_AF_Delete,
            )
            desk.id += 1
            log("cancel", ref=r)
            rc = desk.api.ReqOrderAction(p, desk.id)
            if rc:
                raise RuntimeError(("cancel", rc))

        def done(r, seconds):
            row = until(r, TERMINAL, seconds)
            if not row:
                cancel(r)
                row = until(r, TERMINAL, 8)
            if not row:
                raise RuntimeError("委托终态未知，请按柜台查询；禁止重发")
            return row

        tick = i["PriceTick"]
        log("book", bid=bid, ask=ask, low=low, high=high, stamp=stamp)
        probe = place(td.THOST_FTDC_D_Buy, td.THOST_FTDC_OF_Open, max(low, bid - 10 * tick))
        state = until(probe, TERMINAL | {"3"}, 8)
        if not state or state["OrderStatus"] not in TERMINAL:
            cancel(probe)
            state = until(probe, TERMINAL, 10)
        if not state:
            raise RuntimeError("撤单未到终态")
        if state["VolumeTraded"] == 0:
            if state["OrderStatus"] != "5" or state["OrderSubmitStatus"] == "4":
                raise RuntimeError(("撤单未确认", state))
            bid, ask, low, high, _ = mdspi.book(day)
            if ask * 10 > limit:
                raise ValueError("权利金超过上限")
            opened = done(place(td.THOST_FTDC_D_Buy, td.THOST_FTDC_OF_Open, ask), 10)
            if opened["VolumeTraded"] != 1:
                raise RuntimeError(("买开未成交", opened))
        mid = snapshot(desk)
        log("opened", **mid)
        if (
            mid["active"]
            or sum(
                x["Position"]
                for x in mid["positions"]
                if x["InstrumentID"] == SYMBOL and x["PosiDirection"] == "2"
            )
            != 1
        ):
            raise RuntimeError("柜台未确认一手多仓，停止新委托")
        for attempt in range(2):
            bid, ask, low, high, _ = mdspi.book(day)
            closed = done(
                place(td.THOST_FTDC_D_Sell, td.THOST_FTDC_OF_Close, max(low, bid - tick * (attempt + 1))), 10
            )
            if closed["VolumeTraded"] == 1:
                break
            safe = snapshot(desk)
            if (
                safe["active"]
                or sum(
                    x["Position"]
                    for x in safe["positions"]
                    if x["InstrumentID"] == SYMBOL and x["PosiDirection"] == "2"
                )
                != 1
            ):
                raise RuntimeError("卖平后状态待核对")
        final = snapshot(desk)
        log("final", **final)
        orders = [
            r
            for r in desk.request("QryOrder", **desk.who, InstrumentID=SYMBOL)
            if r["FrontID"] == desk.login["FrontID"]
            and r["SessionID"] == desk.login["SessionID"]
            and r["OrderRef"].strip() in mine
        ]
        ids = {r["OrderSysID"].strip() for r in orders}
        trades = [
            r
            for r in desk.request("QryTrade", **desk.who, InstrumentID=SYMBOL)
            if r["OrderSysID"].strip() in ids
        ]
        log("broker", orders=orders, trades=trades)
        if final["positions"] or final["active"]:
            raise RuntimeError("仓位或委托未清，请查询柜台")
        from .OptionLedger import reconcile

        result = reconcile(initial, mid, final, orders, trades, i["VolumeMultiple"])
        log("reconciled", **result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result["matched"]:
            raise RuntimeError("柜台记录未对齐")
    finally:
        if mdspi:
            mdspi.api.Release()


if __name__ == "__main__":
    main()
