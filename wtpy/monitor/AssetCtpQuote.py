"""One read-only CTP market session owned by one visible quote stream."""
import json
import os
import sys
import threading

from openctp_ctp import thostmduserapi as md

SESSION_MD = "tcp://182.254.243.31:30013"
SIMULATION_MD = "tcp://182.254.243.31:40011"


_output_lock = threading.Lock()

def emit(**values):
    with _output_lock:
        print(json.dumps(values, ensure_ascii=True), flush=True)


def number(value):
    return value if 0 < value < 1e30 else None


class QuoteSpi(md.CThostFtdcMdSpi):
    def __init__(self, api, symbols, mode, sink):
        super().__init__()
        self.api, self.mode, self.sink = api, mode, sink
        self.symbols = {ticker:exchange for exchange,ticker in symbols}
        self.ids = [ticker.encode("ascii") for ticker in self.symbols]

    def OnFrontConnected(self):
        request = md.CThostFtdcReqUserLoginField()
        if self.mode == "session" and os.environ.get("WT_QUOTE_CTP_FRONT"):
            request.BrokerID = os.getenv("WT_QUOTE_CTP_BROKER", "")
            request.UserID = os.getenv("WT_QUOTE_CTP_USER", "")
            request.Password = os.getenv("WT_QUOTE_CTP_PASSWORD", "")
        if self.api.ReqUserLogin(request, 1):
            self.sink(error="CTP 行情登录请求失败")

    def OnRspUserLogin(self, login, info, request_id, last):
        if info and info.ErrorID:
            self.sink(error="CTP 行情登录失败（%d）" % info.ErrorID)
        elif self.api.SubscribeMarketData(self.ids, len(self.ids)):
            self.sink(error="CTP 行情订阅请求失败")

    def OnRspSubMarketData(self, contract, info, request_id, last):
        if info and info.ErrorID:
            self.sink(error="CTP 拒绝订阅（%d）" % info.ErrorID)
        else:
            self.sink(status="已订阅，等待交易所快照")

    def OnFrontDisconnected(self, reason):
        self.sink(status="行情前置断开，等待重新登录")

    def OnRtnDepthMarketData(self, tick):
        exchange = self.symbols.get(tick.InstrumentID)
        if not exchange or (tick.ExchangeID and tick.ExchangeID != exchange):
            return
        source = "SimNow 7×24 仿真回放" if self.mode == "simulation" else "CTP 交易时段前置"
        self.sink(symbol=exchange+"."+tick.InstrumentID, source=source, last=number(tick.LastPrice), bid=number(tick.BidPrice1),
             ask=number(tick.AskPrice1), bid_size=tick.BidVolume1, ask_size=tick.AskVolume1,
             time=(tick.ActionDay + " " + tick.UpdateTime + ".%03d" % tick.UpdateMillisec).strip(),
             trading_day=tick.TradingDay, volume=tick.Volume, oi=number(tick.OpenInterest), amount=number(tick.Turnover))


def run_session(symbols, mode, sink, consume):
    api = md.CThostFtdcMdApi.CreateFtdcMdApi()
    spi = QuoteSpi(api, symbols, mode, sink)
    api.RegisterSpi(spi)
    api.RegisterFront(SIMULATION_MD if mode=="simulation" else os.environ.get("WT_QUOTE_CTP_FRONT", SESSION_MD))
    api.Init()
    try:
        # The monitor closes stdin on disconnect or symbol change.
        consume()
    finally:
        api.UnSubscribeMarketData(spi.ids, len(spi.ids))
        api.Release()


if __name__ == "__main__":
    exchange, ticker, mode = sys.argv[1:]
    run_session([(exchange,ticker)],mode,emit,sys.stdin.buffer.read)
