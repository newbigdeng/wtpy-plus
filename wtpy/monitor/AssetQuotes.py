"""Single-instrument public snapshots. Exchange timestamps survive polling."""
from datetime import datetime, timedelta, timezone
import json
import math
import os
from urllib.request import Request, urlopen

from .AssetMarkets import instrument

CHINA = timezone(timedelta(hours=8))


def price(value):
    number = float(value or 0)
    return number if math.isfinite(number) and 0 < number < 1e30 else None


def read_url(url):
    with urlopen(Request(url, headers={"User-Agent": "WT-MarketMonitor/1.0"}), timeout=6) as response:
        return response.read(65536)


def http_quote(asset, symbol):
    exchange, ticker = instrument(asset, symbol)
    if asset == "crypto":
        relay = os.environ.get("WT_COINBASE_RELAY")
        if relay:
            return json.loads(read_url(relay.rstrip("/") + "/ticker/" + ticker))
        return coinbase_quote(ticker)
    prefix = "sh" if exchange == "SSE" else "sz"
    raw = read_url("https://qt.gtimg.cn/q=" + prefix + ticker).decode("gbk")
    header, _, body = raw.partition('="')
    fields = body.split("~")
    if header.strip() != "v_" + prefix + ticker or len(fields) < 31 or fields[2] != ticker:
        raise ValueError("行情源未返回该股票")
    stamp = fields[30]
    fmt = "%Y%m%d%H%M%S" if stamp.isdigit() else "%Y-%m-%d %H:%M:%S"
    asof = datetime.strptime(stamp, fmt).replace(tzinfo=CHINA)
    return dict(source="腾讯证券行情", last=price(fields[3]), bid=price(fields[9]),
                ask=price(fields[19]), time=asof.isoformat())


def coinbase_quote(ticker):
    _, ticker = instrument("crypto", ticker)
    data = json.loads(read_url("https://api.exchange.coinbase.com/products/" + ticker + "/ticker"))
    return dict(source="Coinbase Exchange", last=price(data["price"]), bid=price(data["bid"]),
                ask=price(data["ask"]), time=data["time"])


def make_coinbase_relay():
    """Run behind loopback when only the data process has an authenticated egress proxy."""
    from fastapi import FastAPI, HTTPException
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/ticker/{product}")
    def ticker(product: str):
        try:
            return coinbase_quote(product)
        except (OSError, ValueError, KeyError):
            raise HTTPException(502, "Coinbase 行情源不可用") from None
    return app
