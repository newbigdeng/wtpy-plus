"""Synchronous request/response adapter for the isolated SimNow option exercise."""

from queue import Queue
from threading import Event
import time
from openctp_ctp import thosttraderapi as td
from wtpy.monitor.CommodityOptionSim import simnow_profile

FIELDS = dict(
    Authenticate="",
    UserLogin="FrontID SessionID TradingDay MaxOrderRef",
    QryInstrument="InstrumentID ExchangeID ProductClass UnderlyingInstrID StrikePrice VolumeMultiple PriceTick IsTrading ExpireDate OptionsType",
    QrySettlementInfoConfirm="ConfirmDate",
    SettlementInfoConfirm="ConfirmDate",
    QryInvestorPosition="InstrumentID PosiDirection Position TodayPosition StrikeFrozen LongFrozen ShortFrozen",
    QryTradingAccount="CurrencyID Available Balance CashIn Commission CurrMargin FrozenCash FrozenMargin",
    QryOrder="InstrumentID ExchangeID OrderRef OrderSysID FrontID SessionID OrderStatus OrderSubmitStatus Direction CombOffsetFlag LimitPrice VolumeTotalOriginal VolumeTraded StatusMsg",
    QryTrade="InstrumentID ExchangeID OrderSysID TradeID TradeType Direction OffsetFlag Volume Price TradeDate TradeTime",
    QryInvestorPositionDetail="InstrumentID TradeID TradeType Direction OpenDate OpenPrice Volume CloseVolume",
    QryInstrumentMarginRate="InstrumentID IsRelative LongMarginRatioByMoney LongMarginRatioByVolume ShortMarginRatioByMoney ShortMarginRatioByVolume",
    QryExecOrder="InstrumentID ExchangeID ExecOrderSysID ExecOrderRef FrontID SessionID OrderSubmitStatus StatusMsg ExecResult ActionType Volume",
    QrySettlementInfo="TradingDay SequenceNo Content",
    QryOptionInstrTradeCost="InstrumentID FixedMargin MiniMargin Royalty ExchFixedMargin ExchMiniMargin",
    QryOptionInstrCommRate="InstrumentID OpenRatioByMoney OpenRatioByVolume CloseRatioByMoney CloseRatioByVolume",
)


def field(name, **values):
    obj = getattr(td, "CThostFtdc" + name + "Field")()
    for k, v in values.items():
        setattr(obj, k, v)
    return obj


def extract(obj, names):
    return {k: getattr(obj, k) for k in names.split()} if obj else {}


class Desk(td.CThostFtdcTraderSpi):
    def __init__(self, path):
        super().__init__()
        self.profile = simnow_profile(path)
        if not self.profile["front"].endswith(":40001"):
            raise ValueError("仅允许 SimNow 7×24")
        self.api = td.CThostFtdcTraderApi.CreateFtdcTraderApi()
        self.events = Queue()
        self.online = Event()
        self.id = 0
        self.api.RegisterSpi(self)
        self.api.SubscribePrivateTopic(td.THOST_TERT_QUICK)
        self.api.RegisterFront(self.profile["front"])
        self.api.Init()

    def OnFrontConnected(self):
        self.online.set()

    def OnFrontDisconnected(self, reason):
        self.events.put(("disconnect", reason))

    def OnRtnOrder(self, r):
        self.events.put(("order", extract(r, FIELDS["QryOrder"])))

    def OnRtnTrade(self, r):
        self.events.put(("trade", extract(r, FIELDS["QryTrade"])))

    def OnRtnExecOrder(self, r):
        self.events.put(("exercise", extract(r, FIELDS["QryExecOrder"])))

    def OnRspExecOrderInsert(self, r, i, n, last):
        if i and i.ErrorID:
            self.events.put(("reject", i.ErrorID, i.ErrorMsg))

    def OnErrRtnExecOrderInsert(self, r, i):
        self.OnRspExecOrderInsert(r, i, 0, True)

    OnRspExecOrderAction = OnRspExecOrderInsert
    OnErrRtnExecOrderAction = OnErrRtnExecOrderInsert

    def OnRspOrderInsert(self, r, i, n, last):
        if i and i.ErrorID:
            self.events.put(("insert_reject", r.OrderRef.strip(), i.ErrorID, i.ErrorMsg))

    def OnErrRtnOrderInsert(self, r, i):
        self.OnRspOrderInsert(r, i, 0, True)

    def OnRspOrderAction(self, r, i, n, last):
        if i and i.ErrorID:
            self.events.put(("reject", i.ErrorID, i.ErrorMsg))

    def OnErrRtnOrderAction(self, r, i):
        self.OnRspOrderAction(r, i, 0, True)

    def take(self, timeout=15):
        e = self.events.get(timeout=timeout)
        if e[0] in ("disconnect", "reject"):
            raise RuntimeError(e)
        if e[0] == "insert_reject":
            raise OrderRejected(*e[1:])
        if e[0] in ("order", "trade", "exercise") and hasattr(self, "on_notification"):
            self.on_notification(*e)
        return e

    def request(self, kind, **values):
        if kind.startswith("Qry"):
            time.sleep(1.05)
        self.id += 1
        input_name = {"Authenticate": "ReqAuthenticate", "UserLogin": "ReqUserLogin"}.get(kind, kind)
        rc = getattr(self.api, "Req" + kind)(field(input_name, **values), self.id)
        if rc:
            raise RuntimeError((kind, rc))
        rows = []
        end = time.monotonic() + 18
        while time.monotonic() < end:
            e = self.take(max(0.1, end - time.monotonic()))
            if e[:2] != ("response", self.id):
                continue
            _, _, row, error, last = e
            if error:
                raise RuntimeError((kind, error))
            if row:
                rows.append(row)
            if last:
                return rows
        raise TimeoutError(kind)

    @property
    def who(self):
        return dict(BrokerID=self.profile["broker"], InvestorID=self.profile["user"])

    def connect(self):
        if not self.online.wait(12):
            raise TimeoutError("front")
        p = self.profile
        self.request(
            "Authenticate", BrokerID=p["broker"], UserID=p["user"], AppID=p["appid"], AuthCode=p["authcode"]
        )
        self.login = self.request("UserLogin", BrokerID=p["broker"], UserID=p["user"], Password=p["pass"])[0]

    def close(self):
        self.api.Release()


def responder(kind):
    def response(self, obj, info, rid, last):
        error = (info.ErrorID, info.ErrorMsg) if info and info.ErrorID else None
        self.events.put(("response", rid, extract(obj, FIELDS[kind]), error, last))

    return response


for kind in FIELDS:
    setattr(Desk, "OnRsp" + kind, responder(kind))


class OrderRejected(RuntimeError):
    """A negative insert acknowledgment, distinct from an unknown order state."""
