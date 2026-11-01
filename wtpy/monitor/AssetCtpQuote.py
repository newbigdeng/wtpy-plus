"""One read-only CTP market session owned by one visible quote stream."""
import json
import os
import sys

from openctp_ctp import thostmduserapi as md

SIMNOW_MD = "tcp://182.254.243.31:40011"


def emit(**values):
    print(json.dumps(values, ensure_ascii=True), flush=True)


def number(value):
    return value if 0 < value < 1e30 else None


class QuoteSpi(md.CThostFtdcMdSpi):
    def __init__(self, api, exchange, ticker):
        super().__init__()
        self.api, self.exchange, self.ticker = api, exchange, ticker

    def OnFrontConnected(self):
        request = md.CThostFtdcReqUserLoginField()
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
        source = "SimNow 仿真行情" if os.environ.get("WT_QUOTE_CTP_FRONT", SIMNOW_MD) == SIMNOW_MD else "CTP"
        emit(source=source, last=number(tick.LastPrice), bid=number(tick.BidPrice1),
             ask=number(tick.AskPrice1), bid_size=tick.BidVolume1, ask_size=tick.AskVolume1,
             time=(tick.ActionDay + " " + tick.UpdateTime + ".%03d" % tick.UpdateMillisec).strip(),
             trading_day=tick.TradingDay)


def main():
    api = md.CThostFtdcMdApi.CreateFtdcMdApi()
    exchange, ticker = sys.argv[1:]
    spi = QuoteSpi(api, exchange, ticker)
    api.RegisterSpi(spi)
    api.RegisterFront(os.environ.get("WT_QUOTE_CTP_FRONT", SIMNOW_MD))
    api.Init()
    try:
        # The monitor closes stdin on disconnect or symbol change.
        sys.stdin.buffer.read()
    finally:
        api.UnSubscribeMarketData([ticker.encode("ascii")], 1)
        api.Release()


if __name__ == "__main__":
    main()
