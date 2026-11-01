"""DCE vertical spreads, exercise declarations and settlement on the SimNow 7×24 counter."""

import argparse
import json
from math import isfinite
import os
from pathlib import Path
from queue import Empty
import time

from .OptionFrames import read_option_frame
from .OptionSimCounter import Desk, OrderRejected, field, td
from .AssetMarkets import instrument

TERMINAL = {"0", "2", "4", "5"}


def positive(number):
    if not isfinite(number) or number <= 0:
        raise ValueError("需要有限正数")
    return number


def reserve(cost, qty):
    # Conservative cash reserve: do not deduct premium income or assume spread margin credits.
    fixed = positive(cost["FixedMargin"])
    royalty = positive(cost["Royalty"])
    return qty * (max(fixed, cost["MiniMargin"]) + royalty)


def covered_spread(qty, buy, sell, reduce):
    """Each IOC callback must return a *confirmed terminal* cumulative fill, or raise."""

    def filled(send, size):
        count = send(size)
        if not isinstance(count, int) or not 0 <= count <= size:
            raise ValueError("柜台成交量异常")
        return count

    opened = filled(buy, qty)
    paired = filled(sell, opened) if opened else 0
    closed = filled(reduce, opened - paired) if opened > paired else 0
    return dict(paired=paired, residual_long=opened - paired - closed)


def expiry_loss(a, b, buy, sell, qty):
    """Worst paired payoff across strikes, plus the unpaired long premium while entering."""
    signed = (a["StrikePrice"] - b["StrikePrice"]) * (1 if a["OptionsType"] == "1" else -1)
    premium = max(buy, buy - sell + max(signed, 0))
    return qty * a["VolumeMultiple"] * premium


def delivery(before, after, call, holder, qty):
    """Return expected and observed net futures change, not a substitute for a clearing report."""
    if holder not in ("long", "short") or qty < 1:
        raise ValueError("行权或指派数量无效")
    side = (1 if call else -1) * (1 if holder == "long" else -1)
    return dict(
        expected_option=-qty,
        actual_option=after[holder] - before[holder],
        expected_future=side * qty,
        actual_future=after["future_net"] - before["future_net"],
    )


class Lifecycle:
    def __init__(self, desk, root, journal):
        self.desk, self.root, self.journal = desk, root, journal
        self.ref = int(desk.login["MaxOrderRef"].strip() or 0)

    def query(self, kind, **v):
        return self.desk.request("Qry" + kind, **self.desk.who, **v)

    def contract(self, symbol):
        ex, ticker = instrument("option", symbol)
        if ex != "DCE":
            raise ValueError("仅支持大商所商品期权")
        rows = self.desk.request("QryInstrument", ExchangeID=ex, InstrumentID=ticker)
        if (
            len(rows) != 1
            or rows[0]["InstrumentID"] != ticker
            or rows[0]["ProductClass"] != td.THOST_FTDC_PC_Options
        ):
            raise ValueError("柜台没有该期权")
        return rows[0]

    def positions(self, c):
        rows = self.query("InvestorPosition")

        def count(ticker, d):
            return sum(r["Position"] for r in rows if r["InstrumentID"] == ticker and r["PosiDirection"] == d)

        option, future = c["InstrumentID"], c["UnderlyingInstrID"]
        return dict(
            long=count(option, "2"),
            short=count(option, "3"),
            future_net=count(future, "2") - count(future, "3"),
            frozen_long=sum(
                max(r["StrikeFrozen"], 0) + max(r["ShortFrozen"], 0) + max(r["LongFrozen"], 0)
                for r in rows
                if r["InstrumentID"] == option and r["PosiDirection"] == "2"
            ),
        )

    def terms(self, c, price, forward):
        (row,) = self.query(
            "OptionInstrTradeCost",
            ExchangeID="DCE",
            InstrumentID=c["InstrumentID"],
            HedgeFlag="1",
            InputPrice=positive(price),
            UnderlyingPrice=positive(forward),
        )
        return row

    def available(self):
        accounts = self.query("TradingAccount")
        return next(a["Available"] for a in accounts if a["CurrencyID"] == "CNY")

    def submit(self, name, msg):
        self.desk.id += 1
        rc = getattr(self.desk.api, "Req" + name)(msg, self.desk.id)
        if rc:
            raise RuntimeError((name, rc))

    def wait(self, kind, ref, finished):
        end = time.monotonic() + 12
        while time.monotonic() < end:
            try:
                event = self.desk.take(max(0.01, end - time.monotonic()))
            except Empty:
                break
            if event[0] != kind:
                continue
            r = event[1]
            if (r["FrontID"], r["SessionID"]) != (self.desk.login["FrontID"], self.desk.login["SessionID"]):
                continue
            if r["OrderRef" if kind == "order" else "ExecOrderRef"].strip() != ref:
                continue
            self.journal(kind, r)
            if finished(r):
                return r
        raise RuntimeError("柜台终态未知：只查询，不重发")

    def ioc(self, c, qty, side, offset, price):
        self.ref += 1
        ref = str(self.ref)
        positive(price)
        tick = c["PriceTick"]
        if abs(price / tick - round(price / tick)) > 1e-6:
            raise ValueError("限价未对齐最小价位")
        kwargs = dict(
            ExchangeID="DCE",
            InstrumentID=c["InstrumentID"],
            OrderRef=ref,
            Direction=side,
            CombOffsetFlag=offset,
            VolumeTotalOriginal=qty,
            LimitPrice=price,
        )
        self.journal("ioc", kwargs)
        self.submit(
            "OrderInsert",
            field(
                "InputOrder",
                **self.desk.who,
                **kwargs,
                OrderPriceType=td.THOST_FTDC_OPT_LimitPrice,
                CombHedgeFlag="1",
                TimeCondition=td.THOST_FTDC_TC_IOC,
                VolumeCondition=td.THOST_FTDC_VC_AV,
                MinVolume=1,
                ContingentCondition=td.THOST_FTDC_CC_Immediately,
                ForceCloseReason=td.THOST_FTDC_FCC_NotForceClose,
            ),
        )
        try:
            order = self.wait("order", ref, lambda r: r["OrderStatus"] in TERMINAL)
        except OrderRejected as exc:
            if exc.args[0] != ref:
                raise
            self.journal("rejected", list(exc.args))
            return 0
        if order["VolumeTraded"]:
            trades = self.query("Trade", ExchangeID="DCE", InstrumentID=c["InstrumentID"])
            fills = {r["TradeID"]: r for r in trades if r["OrderSysID"] == order["OrderSysID"]}
            if sum(t["Volume"] for t in fills.values()) != order["VolumeTraded"]:
                raise RuntimeError("成交与终态不一致，暂停组合并查柜台")
            self.journal("fills", list(fills.values()))
        return order["VolumeTraded"]

    def spread(self, a, b, session, qty, budget, loss_limit, apply):
        if (a["UnderlyingInstrID"], a["ExpireDate"], a["OptionsType"]) != (
            b["UnderlyingInstrID"],
            b["ExpireDate"],
            b["OptionsType"],
        ) or a["StrikePrice"] == b["StrikePrice"]:
            raise ValueError("两腿必须是同标的、同到期、同方向的不同行权价")
        if any(not c["IsTrading"] or c["ExpireDate"] <= self.desk.login["TradingDay"] for c in (a, b)):
            raise ValueError("合约不可交易或已到期")
        keys = ["DCE." + c["InstrumentID"] for c in (a, b)]

        def quotes():
            f = read_option_frame(self.root, session, mode="simulation", symbols=keys)
            if f["trading_day"] != self.desk.login["TradingDay"]:
                raise ValueError("会话与柜台交易日不一致")
            return {r["symbol"]: r for r in f["rows"]}

        def funds(q, include_buy):
            books = quotes()
            left, right = (books[k] for k in keys)
            long_price = left["ask"] if include_buy else entry_price
            if expiry_loss(a, b, long_price, right["bid"], q) > positive(loss_limit):
                raise ValueError("单腿权利金或到期价差风险超出亏损限额")
            cost = reserve(self.terms(b, right["bid"], right["forward"]), q)
            cash = q * left["ask"] * a["VolumeMultiple"] if include_buy else 0
            if cost + cash > min(positive(budget), self.available()):
                raise ValueError("可用资金或预算不足以覆盖两腿")
            return cost + cash, left["ask"], right["bid"]

        if any(r["Position"] for r in self.query("InvestorPosition")) or any(
            r["OrderStatus"] not in TERMINAL for r in self.query("Order")
        ):
            raise ValueError("此独立仿真组合要求初始空仓、无活动委托")
        if a["VolumeMultiple"] != b["VolumeMultiple"]:
            raise ValueError("两腿乘数不同")
        cap, entry_price, _ = funds(qty, True)
        if not apply:
            return dict(conservative_reserve=cap)

        def sell(q):
            try:
                _, _, price = funds(q, False)
            except (ValueError, OSError):
                return 0
            return self.ioc(b, q, "1", "0", price)

        def reduce(q):
            try:
                price = quotes()[keys[0]]["bid"]
            except (ValueError, OSError):
                return 0
            return self.ioc(a, q, "1", "1", price)

        def buy(q):
            price = quotes()[keys[0]]["ask"]
            if price > entry_price:
                return 0
            return self.ioc(a, q, "0", "0", price)

        result = covered_spread(qty, buy, sell, reduce)
        result.update(long_position=self.positions(a)["long"], short_position=self.positions(b)["short"])
        result["matched"] = (
            result["long_position"] == result["paired"] + result["residual_long"]
            and result["short_position"] == result["paired"]
        )
        result["complete"] = result["matched"] and result["paired"] == qty and not result["residual_long"]
        return result

    def future_ask(self, c):
        from .OptionBuyerSim import Quote

        feed = Quote(c["UnderlyingInstrID"])
        try:
            return feed.book(self.desk.login["TradingDay"])[1]
        finally:
            feed.api.Release()

    def declaration(self, c, qty, abandon, apply, budget=None):
        before = self.positions(c)
        if qty > before["long"] - before["frozen_long"]:
            raise ValueError("可用买方持仓不足")
        if self.desk.login["TradingDay"] > c["ExpireDate"]:
            raise ValueError("已过到期日")
        required = None
        if not abandon:
            # Use the higher of the strike and observed future as a conservative notional.
            price = max(c["StrikePrice"], self.future_ask(c))
            (rate,) = self.query(
                "InstrumentMarginRate", ExchangeID="DCE", InstrumentID=c["UnderlyingInstrID"], HedgeFlag="1"
            )
            if rate["IsRelative"]:
                raise ValueError("柜台只返回相对保证金率，请先取得绝对标准")
            side = "Long" if c["OptionsType"] == "1" else "Short"
            required = positive(
                (
                    price * c["VolumeMultiple"] * rate[side + "MarginRatioByMoney"]
                    + rate[side + "MarginRatioByVolume"]
                )
                * qty
            )
            if required >= min(positive(budget), self.available()):
                raise ValueError("行权后期货保证金超过预算或可用资金")
        self.journal(
            "before",
            dict(symbol="DCE." + c["InstrumentID"], position=before, day=self.desk.login["TradingDay"]),
        )
        if not apply:
            return dict(preview=True, position=before, estimated_future_margin=required)
        self.ref += 1
        ref = str(self.ref)
        args = dict(
            ExchangeID="DCE",
            InstrumentID=c["InstrumentID"],
            ExecOrderRef=ref,
            Volume=qty,
            OffsetFlag=td.THOST_FTDC_OF_Close,
            HedgeFlag="1",
            PosiDirection=td.THOST_FTDC_PD_Long,
            ActionType=td.THOST_FTDC_ACTP_Abandon if abandon else td.THOST_FTDC_ACTP_Exec,
            ReservePositionFlag=td.THOST_FTDC_EOPF_Reserve,
            CloseFlag=td.THOST_FTDC_EOCF_NotToClose,
        )
        self.journal("request", args)
        self.submit("ExecOrderInsert", field("InputExecOrder", **self.desk.who, **args))
        return self.wait(
            "exercise",
            ref,
            lambda r: r["OrderSubmitStatus"] in ("3", "4") or bool(r["ExecOrderSysID"].strip()),
        )

    def declarations(self, c):
        return self.query("ExecOrder", ExchangeID="DCE", InstrumentID=c["InstrumentID"])

    def cancel_declaration(self, c, sysid, apply):
        (match,) = [
            r
            for r in self.declarations(c)
            if r["ExecOrderSysID"].strip() == sysid and r["ExecResult"] == td.THOST_FTDC_OER_NoExec
        ]
        if not apply:
            return match
        self.journal("cancel", match)
        self.submit(
            "ExecOrderAction",
            field(
                "InputExecOrderAction",
                **self.desk.who,
                ExchangeID="DCE",
                InstrumentID=c["InstrumentID"],
                ExecOrderSysID=match["ExecOrderSysID"],
                ActionFlag=td.THOST_FTDC_AF_Delete,
            ),
        )
        for _ in range(5):
            rows = [r for r in self.declarations(c) if r["ExecOrderSysID"] == match["ExecOrderSysID"]]
            if rows and rows[0]["ExecResult"] != td.THOST_FTDC_OER_NoExec:
                return rows[0]
        raise RuntimeError("行权撤回终态未知，只查询，不重发")


def main():
    import fcntl

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True, help="只读挂载的 SimNow 7×24 通道")
    p.add_argument("--root", type=Path, required=True, help="期权资产目录")
    p.add_argument("--ticket", required=True, help="本次操作唯一记录名")
    sub = p.add_subparsers(dest="action", required=True)
    for action in ("snapshot", "margin", "spread", "exercise", "abandon", "cancel-exercise", "report"):
        cmd = sub.add_parser(action)
        cmd.add_argument("symbol")
        if action in ("spread", "exercise", "abandon", "report"):
            cmd.add_argument("--qty", type=int, required=True)
        if action in ("spread", "exercise", "abandon", "cancel-exercise"):
            cmd.add_argument("--apply", action="store_true")
        if action == "margin":
            cmd.add_argument("--premium", type=float, required=True)
            cmd.add_argument("--future", type=float, required=True)
        if action == "spread":
            cmd.add_argument("--sell", required=True)
            cmd.add_argument("--budget", type=float, required=True)
            cmd.add_argument("--session", required=True)
            cmd.add_argument("--loss-limit", type=float, required=True)
        if action == "exercise":
            cmd.add_argument("--budget", type=float, required=True)
        if action == "cancel-exercise":
            cmd.add_argument("--sysid", required=True)
        if action == "report":
            cmd.add_argument("--baseline", required=True)
            cmd.add_argument("--holder", choices=["long", "short"], required=True)
            cmd.add_argument("--clearing-day", required=True)
    a = p.parse_args()
    if hasattr(a, "qty") and a.qty < 1:
        p.error("手数必须为正整数")

    def safe(name):
        import re

        if not re.fullmatch("[a-zA-Z0-9_-]{1,64}", name):
            p.error("记录名仅限字母、数字、横线和下划线")
        return folder / (name + ".jsonl")

    folder = a.root / "lifecycle"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    with (folder / "counter.lock").open("a") as lock, safe(a.ticket).open("x", encoding="utf8") as stream:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def log(event, body):
            stream.write(json.dumps(dict(event=event, data=body), ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

        desk = Desk(a.profile)
        try:
            desk.connect()
            svc = Lifecycle(desk, a.root, log)
            c = svc.contract(a.symbol)
            account = dict(broker=desk.profile["broker"], user=desk.profile["user"])
            log(
                "context",
                dict(account=account, symbol=a.symbol, day=desk.login["TradingDay"], action=a.action),
            )
            if a.action == "snapshot":
                out = svc.positions(c)
            elif a.action == "margin":
                cost = svc.terms(c, a.premium, a.future)
                out = dict(counter=cost, conservative_reserve_per_lot=reserve(cost, 1))
            elif a.action == "spread":
                out = svc.spread(c, svc.contract(a.sell), a.session, a.qty, a.budget, a.loss_limit, a.apply)
            elif a.action in ("exercise", "abandon"):
                out = svc.declaration(c, a.qty, a.action == "abandon", a.apply, getattr(a, "budget", None))
            elif a.action == "cancel-exercise":
                out = svc.cancel_declaration(c, a.sysid, a.apply)
            else:
                from datetime import datetime

                datetime.strptime(a.clearing_day, "%Y%m%d")
                base = {
                    x["event"]: x["data"]
                    for x in map(json.loads, safe(a.baseline).read_text(encoding="utf8").splitlines())
                }
                ctx = base["context"]
                if (
                    ctx["symbol"] != a.symbol
                    or ctx["account"] != account
                    or ctx["action"] != "snapshot"
                    or not ctx["day"] <= a.clearing_day <= desk.login["TradingDay"]
                ):
                    raise ValueError("基线账户、合约或清算日期不匹配")
                delta = delivery(base["result"], svc.positions(c), c["OptionsType"] == "1", a.holder, a.qty)
                rows = svc.query("SettlementInfo", TradingDay=a.clearing_day)
                statement = "".join(
                    r["Content"]
                    for r in sorted(rows, key=lambda r: r["SequenceNo"])
                    if r["TradingDay"] == a.clearing_day
                )
                future = c["UnderlyingInstrID"]
                details = [
                    r
                    for r in svc.query("InvestorPositionDetail", ExchangeID="DCE", InstrumentID=future)
                    if r["TradeType"] == td.THOST_FTDC_TRDT_OptionsExecution
                    and r["OpenDate"] == a.clearing_day
                ]
                fills = [
                    r
                    for r in svc.query("Trade", ExchangeID="DCE", InstrumentID=future)
                    if r["TradeType"] == td.THOST_FTDC_TRDT_OptionsExecution
                    and r["TradeDate"] == a.clearing_day
                ]
                evidence = safe(a.ticket).with_suffix(".settlement.txt")
                if statement:
                    evidence.write_text(statement, encoding="utf8")
                out = dict(
                    delta=delta,
                    position_matches=(
                        delta["expected_option"] == delta["actual_option"]
                        and delta["expected_future"] == delta["actual_future"]
                    ),
                    declarations=svc.declarations(c),
                    statement_available=bool(statement),
                    clearing_day=a.clearing_day,
                    exercise_futures_trades=fills,
                    exercise_futures_positions=details,
                    note="需逐笔核对结算单。普通期货交易、交割与自动对冲也会改变持仓。",
                )
            log("result", out)
            print(json.dumps(out, ensure_ascii=False))
        finally:
            desk.close()


if __name__ == "__main__":
    main()
