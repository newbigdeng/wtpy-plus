"""One read-only CTP market session owned by one visible quote stream."""
import json
import os
import sys

from openctp_ctp import thostmduserapi as md

SESSION_MD = "tcp://182.254.243.31:30013"
SIMULATION_MD = "tcp://182.254.243.31:40011"


def emit(**values):
    print(json.dumps(values, ensure_ascii=True), flush=True)


def number(value):
    return value if 0 < value < 1e30 else None


class QuoteSpi(md.CThostFtdcMdSpi):
    def __init__(self, api, exchange, ticker, mode):
        super().__init__()
        self.api, self.exchange, self.ticker, self.mode = api, exchange, ticker, mode

    def OnFrontConnected(self):
        request = md.CThostFtdcReqUserLoginField()
        if self.mode == "session" and os.environ.get("WT_QUOTE_CTP_FRONT"):
            request.BrokerID = os.getenv("WT_QUOTE_CTP_BROKER", "")
            request.UserID = os.getenv("WT_QUOTE_CTP_USER", "")
            request.Password = os.getenv("WT_QUOTE_CTP_PASSWORD", "")
        if self.api.ReqUserLogin(request, 1):
            emit(error="CTP 行情登录请求失败")

    def OnRspUserLogin(self, login, info, request_id, last):
        if info and info.ErrorID:
            emit(error="CTP 行情登录失败（%d）" % info.ErrorID)
        elif self.api.SubscribeMarketData([self.ticker.encode("ascii")], 1):
            emit(error="CTP 行情订阅请求失败")

    def OnRspSubMarketData(self, contract, info, request_id, last):
        if info and info.ErrorID:
            emit(error="CTP 拒绝订阅（%d）" % info.ErrorID)
        else:
            emit(status="已订阅，等待交易所快照")

    def OnFrontDisconnected(self, reason):
        emit(status="行情前置断开，等待重新登录")

    def OnRtnDepthMarketData(self, tick):
        if tick.InstrumentID != self.ticker or (tick.ExchangeID and tick.ExchangeID != self.exchange):
            return
        source = "SimNow 7×24 仿真回放" if self.mode == "simulation" else "CTP 交易时段前置"
        emit(source=source, last=number(tick.LastPrice), bid=number(tick.BidPrice1),
             ask=number(tick.AskPrice1), bid_size=tick.BidVolume1, ask_size=tick.AskVolume1,
             time=(tick.ActionDay + " " + tick.UpdateTime + ".%03d" % tick.UpdateMillisec).strip(),
             trading_day=tick.TradingDay)


def main():
    api = md.CThostFtdcMdApi.CreateFtdcMdApi()
    exchange, ticker, mode = sys.argv[1:]
    spi = QuoteSpi(api, exchange, ticker, mode)
    api.RegisterSpi(spi)
    api.RegisterFront(SIMULATION_MD if mode=="simulation" else os.environ.get("WT_QUOTE_CTP_FRONT", SESSION_MD))
    api.Init()
    try:
        # The monitor closes stdin on disconnect or symbol change.
        sys.stdin.buffer.read()
    finally:
        api.UnSubscribeMarketData([ticker.encode("ascii")], 1)
        api.Release()


if __name__ == "__main__":
    main()
