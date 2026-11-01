"""Archive Coinbase matches and materialize minute bars only from recorded trades."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from .AssetMarkets import inside, instrument


def timestamp(ms):
    return int(datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%d%H%M"))


def connect_archive(path, *, read=False):
    if read:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS trades(symbol TEXT, id INTEGER, ms INTEGER, price REAL, size REAL, PRIMARY KEY(symbol,id)) WITHOUT ROWID")
    db.execute("CREATE INDEX IF NOT EXISTS trade_time ON trades(symbol,ms)")
    return db


def prepare_products(root, symbols):
    from wtpy.StrategyRuntime import atomic_json
    from .AssetQuotes import read_url
    directory = inside(root, "common")
    directory.mkdir(parents=True, exist_ok=True)
    products = inside(root, "common", "commodities.json")
    contracts = inside(root, "common", "contracts.json")
    def load(path):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"COINBASE": {}}
    commodities, instruments = load(products), load(contracts)
    for symbol in symbols:
        if symbol in commodities["COINBASE"] and symbol in instruments["COINBASE"]:
            continue
        data = json.loads(read_url("https://api.exchange.coinbase.com/products/" + symbol))
        if data.get("id") != symbol:
            raise ValueError("Coinbase 没有返回所选交易对")
        tick = Decimal(data["quote_increment"]).normalize()
        rules = dict(name=symbol, exchg="COINBASE", category=23, holiday="UTC", session="ALLDAY",
                     covermode=3, pricemode=0, volscale=1, pricetick=float(tick),
                     precision=max(0,-tick.as_tuple().exponent), lotstick=float(data["base_increment"]),
                     minlots=float(data["base_increment"]))
        commodities["COINBASE"].setdefault(symbol, rules)
        instruments["COINBASE"].setdefault(symbol, dict(name=symbol,code=symbol,exchg="COINBASE",rules=rules))
    atomic_json(products, commodities)
    atomic_json(contracts, instruments)
    for name,value in (("sessions",{"ALLDAY":{"name":"UTC","offset":0,"sections":[{"from":0,"to":2400}]}}),
                       ("holidays",{"UTC":[]})):
        path = inside(root,"common",name+".json")
        if not path.exists():
            atomic_json(path,value)


def record(root, symbols):
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed
    from time import sleep
    prepare_products(root, symbols)
    target = inside(root, "storage")
    target.mkdir(parents=True, exist_ok=True)
    with closing(connect_archive(inside(root, "storage", "matches.sqlite"))) as db:
        while True:
            try:
                with connect("wss://ws-feed.exchange.coinbase.com", open_timeout=10) as feed:
                    feed.send(json.dumps({"type": "subscribe", "product_ids": symbols, "channels": ["matches"]}))
                    for raw in feed:
                        item = json.loads(raw)
                        if item["type"] == "error":
                            raise ValueError("Coinbase 拒绝订阅，请检查交易对")
                        if item["type"] not in ("match", "last_match") or item["product_id"] not in symbols:
                            continue
                        ms = int(datetime.fromisoformat(item["time"].replace("Z", "+00:00")).timestamp() * 1000)
                        with db:
                            db.execute("INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?)",
                                (item["product_id"], int(item["trade_id"]), ms, float(item["price"]), float(item["size"])))
            except (OSError, ConnectionClosed, TimeoutError):
                sleep(5)


def catalog(root):
    path = inside(root, "storage", "matches.sqlite")
    if not path.is_file():
        return []
    entries = []
    with closing(connect_archive(path, read=True)) as db:
        for symbol, count, first, last, minutes, fives in db.execute(
                "SELECT symbol,count(*),min(ms),max(ms),count(distinct ms/60000),count(distinct ms/300000) FROM trades GROUP BY symbol"):
            instrument("crypto", symbol)
            for period, rows in (("trades", count), ("m1", minutes), ("m5", fives)):
                entries.append(dict(id=f"crypto_{symbol}_{period}", label=symbol + " · " + period, rows=rows,
                    code="COINBASE." + symbol, period=period, start=timestamp(first), end=timestamp(last),
                    source="Coinbase matches", archive=str(path), ticker=symbol, exchange="COINBASE",
                    basefiles_map=basefiles(root), model="trade_prints" if period=="trades" else "closed_bar_only"))
    return entries


def basefiles(root):
    return {key: str(inside(root, "common", name + ".json")) for key, name in
            (("commodity", "commodities"), ("contract", "contracts"), ("session", "sessions"),
             ("holiday", "holidays"), ("fee", "fees"))}


def replay_bars(path, symbol, period, start, end):
    if period not in ("m1", "m5"):
        raise ValueError("逐笔成交没有买卖盘口，请选择分钟线回测")
    def millis(stamp):
        return int(datetime.strptime(str(stamp), "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp()*1000)
    step = 60000 if period == "m1" else 300000
    begin, stop = millis(start), millis(end) + 60000
    with closing(connect_archive(Path(path), read=True)) as db:
        # Use adjacent prints to distinguish fully observed buckets from partial edges.
        prev = db.execute("SELECT id,ms FROM trades WHERE symbol=? AND ms<? ORDER BY ms DESC,id DESC LIMIT 1",
                          (symbol, begin)).fetchone()
        cursor = db.execute("SELECT id,ms,price,size FROM trades WHERE symbol=? AND ms>=? AND ms<? ORDER BY ms,id",
                            (symbol, begin, stop + step))
        bar, bucket, complete = None, None, bool(prev)
        for trade_id, ms, price, size in cursor:
            key = ms // step
            if prev and trade_id != prev[0] + 1:
                raise ValueError("所选区间存在缺失的成交序号，请缩短回测区间")
            if key != bucket:
                if complete and bar and (bucket + 1) * step <= stop:
                    yield bar
                if ms >= stop:
                    break
                complete = prev is not None and key * step >= begin
                bucket = key
                stamp = timestamp((key+1)*step)
                bar = dict(date=timestamp(key*step)//10000, time=stamp-199000000000,
                           open=price, high=price, low=price, close=price, vol=0., money=0.)
            bar.update(high=max(bar["high"],price), low=min(bar["low"],price),
                       close=price, vol=bar["vol"]+size, money=bar["money"]+size*price)
            prev = (trade_id, ms)
