"""Asset identities shared by the monitor, archive reader and collectors."""
import os
from pathlib import Path
import re

from wtpy.ContractMgr import COMMODITY_EXCHANGES, COMMODITY_OPTION as OPTION, commodity_option_code

ASSETS = ("stock", "futures", "option", "crypto")
FUTURE = re.compile(r"([A-Za-z]{1,3})(\d{3,4})")


def asset_root(deploy, asset):
    if asset not in ASSETS:
        raise ValueError("资产类别不存在")
    parent = Path(os.environ.get("WT_ASSET_ROOT") or Path(deploy) / "assets").resolve()
    root = parent / asset
    if root.resolve() != root:
        raise ValueError("资产目录不能指向其他工作区")
    return root


def inside(root, *parts):
    path = root.joinpath(*parts)
    if path.resolve() != path:
        raise ValueError("资产目录不能跨区链接")
    return path


def instrument(asset, symbol):
    symbol = symbol.strip()
    exchange, sep, ticker = symbol.partition(".")
    if asset == "crypto" and re.fullmatch(r"[A-Z0-9]{1,12}-[A-Z0-9]{1,10}", symbol):
        return "COINBASE", symbol
    if asset == "stock" and exchange in ("SSE", "SZSE") and re.fullmatch(r"\d{6}", ticker):
        return exchange, ticker
    if asset == "futures" and exchange in (*COMMODITY_EXCHANGES, "CFFEX"):
        # Accept both the exchange instrument ID and WT's dotted futures code.
        if re.fullmatch(r"[A-Za-z]{1,3}\.\d{4}", ticker):
            product, month = ticker.split(".")
            ticker = product + (month[1:] if exchange == "CZCE" else month)
        if FUTURE.fullmatch(ticker):
            return exchange, ticker
    if asset == "option" and exchange in COMMODITY_EXCHANGES and OPTION.fullmatch(ticker):
        return exchange, ticker
    raise ValueError("代码与资产类别不匹配，请使用交易所的合约代码")


def dataset_code(asset, exchange, ticker, info):
    if asset == "futures":
        product = info.get("product", "")
        if not product or not ticker.startswith(product) or "option" in info:
            return None
        from wtpy.ContractCodes import contract_standard_code
        try:return contract_standard_code(exchange,ticker,info)
        except ValueError:return None
    try:
        instrument(asset, ticker if asset == "crypto" else exchange + "." + ticker)
    except ValueError:
        return None
    if asset == "crypto" and exchange not in ("COINBASE","BINANCE"):
        return None
    if asset == "option":
        return commodity_option_code(exchange, ticker, info.get("expiredate")) if info.get("option") else None
    # Historical replayer resolves EXCHANGE.PRODUCT.RAWID back to the stored ID.
    product = info.get("product")
    return ".".join((exchange, product, ticker)) if product else exchange + "." + ticker
