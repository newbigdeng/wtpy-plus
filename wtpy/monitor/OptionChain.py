"""Import a public contract directory for display, separate from executable WT basefiles."""
from datetime import datetime, timezone
import json
from pathlib import Path

from .AssetMarkets import COMMODITY_EXCHANGES, OPTION, inside, instrument


def import_directory(root, filename):
    from wtpy.StrategyRuntime import atomic_json
    payload = json.loads(Path(filename).read_bytes())
    if payload.get("rsp_code") != 0:
        raise ValueError("OpenCTP 没有返回合约目录")
    chains = {}
    today = datetime.now(timezone.utc).date().isoformat()
    for item in payload["data"]:
        exchange, code = item["ExchangeID"], item["InstrumentID"]
        match = OPTION.fullmatch(code)
        if exchange not in COMMODITY_EXCHANGES or not match or item["ExpireDate"] < today:
            continue
        product, month, _, side, strike = match.groups()
        if item["UnderlyingInstrID"] != product + month or float(item["StrikePrice"]) != float(strike):
            continue
        key = exchange + "." + item["UnderlyingInstrID"]
        chains.setdefault(key, []).append(dict(symbol=exchange+"."+code, side="看涨" if side=="C" else "看跌",
            strike=float(strike), expiry=int(item["ExpireDate"].replace("-",""))))
    if not chains:
        raise ValueError("目录中没有有效的国内商品期权")
    directory=inside(root,"reference")
    directory.mkdir(parents=True,exist_ok=True)
    atomic_json(inside(root,"reference","chain.json"),
                {"source":"OpenCTP 公开合约字典","imported":today,"chains":chains})
    return sum(map(len,chains.values()))


def chain(root, underlying):
    from .MarketSubscriptions import _load
    exchange, future = instrument("futures", underlying)
    key = exchange + "." + future
    native = inside(root, "common", "contracts.json")
    rows = []
    if native.is_file():
        for code, info in _load(native).get(exchange, {}).items():
            option = info.get("option", {})
            match = OPTION.fullmatch(code)
            if match and option.get("underlying") == future:
                rows.append(dict(symbol=exchange+"."+code, side="看涨" if match[4]=="C" else "看跌",
                                 strike=float(option["strikeprice"]), expiry=info.get("expiredate")))
    source = "WT 本地合约表"
    if not rows:
        path = inside(root,"reference","chain.json")
        if path.is_file():
            reference = json.loads(path.read_text(encoding="utf-8"))
            rows = reference["chains"].get(key, [])
            source = reference["source"] + " · 导入 " + reference["imported"]
    return dict(contracts=sorted(rows,key=lambda r:(r["expiry"] or 0,r["strike"],r["side"])),source=source)


if __name__ == "__main__":
    import argparse
    import os
    from .AssetMarkets import asset_root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file",help="OpenCTP instruments?types=option 的 JSON 响应")
    parser.add_argument("--deploy",default=os.environ.get("WT_DATA_DIR","/data"))
    args = parser.parse_args()
    print(import_directory(asset_root(args.deploy,"option"),args.file))
