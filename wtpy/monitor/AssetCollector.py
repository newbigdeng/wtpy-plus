"""Run one independent archival process. The working directory belongs to its asset."""
import argparse
import os
from pathlib import Path

from .AssetMarkets import asset_root, inside, instrument
from .MarketSubscriptions import _load


def native_config(root, asset, symbols):
    from .AssetMarkets import dataset_code
    common = inside(root, "common")
    contracts = _load(inside(root, "common", "contracts.json"))
    parser = _load(inside(root, "datahub", "feed.json"))
    required = "ParserXTP" if asset == "stock" else "ParserCTP"
    if parser.get("module") != required:
        raise ValueError("该资产采集器需要 " + required)
    codes = []
    for symbol in symbols:
        exchange, ticker = instrument(asset, symbol)
        row = contracts.get(exchange, {}).get(ticker)
        if row is None or not dataset_code(asset, exchange, ticker, row):
            raise ValueError("合约不在该资产的基础资料中：" + symbol)
        codes.append(exchange + "." + ticker)
    bases = {"commodity":"commodities.json", "contract":"contracts.json", "session":"sessions.json", "holiday":"holidays.json"}
    for name in bases.values():
        if not inside(root, "common", name).is_file():
            raise ValueError("资产基础资料缺少 " + name)
    config = dict(basefiles={**{k: str(common/v) for k,v in bases.items()}, "utf-8":True},
        statemonitor=str(inside(root, "datahub", "statemonitor.yaml")),
        writer=dict(module="WtDataStorage", path=str(inside(root, "storage")), disabletick=False,
                    disablemin1=False, disablemin5=False, disablehis=False),
        parsers=[{**parser, "active":True, "id":asset, "code":",".join(codes)}])
    if not Path(config["statemonitor"]).is_file():
        raise ValueError("请为该资产配置 datahub/statemonitor.yaml 收盘转储时段")
    return config


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", choices=("stock","option","crypto"))
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--deploy", default=os.environ.get("WT_DATA_DIR", "/data"))
    args = parser.parse_args()
    root = asset_root(args.deploy, args.asset)
    folder = inside(root, "datahub")
    folder.mkdir(parents=True, exist_ok=True)
    os.chdir(folder)
    symbols = list(dict.fromkeys(args.symbols))
    for symbol in symbols:
        instrument(args.asset, symbol)
    with inside(root, "datahub", "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.asset == "crypto":
            from .CryptoArchive import record
            record(root, symbols)
        else:
            from wtpy import WtDtEngine
            from wtpy.StrategyRuntime import atomic_json
            config = native_config(root, args.asset, symbols)
            # The read-side catalog needs only data locations, never provider credentials.
            atomic_json(inside(root, "datahub", "catalog.json"),
                        {k:config[k] for k in ("writer", "basefiles")})
            engine = WtDtEngine()
            engine.init_with_config(config, {"root":{"level":"info","sinks":[{"type":"console_sink"}]}})
            engine.run(False)


if __name__ == "__main__":
    main()
