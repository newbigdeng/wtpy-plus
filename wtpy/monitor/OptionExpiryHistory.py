"""Build an option-workspace packet from exact DCE contracts, never a continuous series."""

import argparse
from datetime import date
import json
from pathlib import Path
import re
from urllib.request import urlopen

from .MarketHistory import TushareHistory


def daily(symbol, option):
    host, api = (
        ("stock", "FutureOptionAllService.getOptionDayline")
        if option
        else ("stock2", "InnerFuturesNewService.getDailyKLine")
    )
    url = f"https://{host}.finance.sina.com.cn/futures/api/jsonp.php/var%20_q=/{api}?symbol={symbol}"
    with urlopen(url, timeout=15) as response:
        body = response.read().split(b"=(", 1)[-1].split(b");", 1)[0]
    return json.loads(body) or []


def fetch(provider, codes, start, end):
    if not 0 < (date.fromisoformat(end) - date.fromisoformat(start)).days <= 366:
        raise ValueError("一次请选择一年内的跨期区间")
    params = dict(start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    days = sorted(
        r["cal_date"]
        for r in provider.request("trade_cal", dict(exchange="DCE", is_open="1", **params), "cal_date")
    )
    if not days:
        raise ValueError("区间内无交易日")
    futures = {d: {} for d in days}
    contracts, loaded = {}, set()
    for code in dict.fromkeys(codes):
        match = re.fullmatch(r"(M\d{4})-([CP])-(\d+)\.DCE", code)
        if not match:
            raise ValueError("仅支持具体豆粕期权合约，例如 M2409-C-2900.DCE")
        f, kind, strike = match.groups()
        rows = provider.request(
            "opt_basic",
            dict(exchange="DCE", ts_code=code),
            "ts_code,call_put,exercise_price,maturity_date,per_unit",
        )
        if len(rows) != 1 or rows[0]["ts_code"] != code:
            raise ValueError(f"{code} 缺合约资料")
        meta = rows[0]
        if (meta["call_put"], meta["exercise_price"], meta["per_unit"]) != (kind, int(strike), 10):
            raise ValueError(f"{code} 合约乘数或条款不符")
        bars = {}
        for r in daily(f.lower() + kind + strike, True):
            d = r["d"].replace("-", "")
            if d in futures:
                bars[d] = dict(
                    open=float(r["o"]),
                    high=float(r["h"]),
                    low=float(r["l"]),
                    close=float(r["c"]),
                    volume=int(r["v"]),
                )
        contracts[code] = dict(
            future=f + ".DCE", kind=kind, strike=int(strike), expiry=meta["maturity_date"], mult=10, bars=bars
        )
        if f not in loaded:
            for r in daily(f, False):
                d = r["d"].replace("-", "")
                if d in futures and r.get("s") and float(r["s"]) > 0:
                    futures[d][f + ".DCE"] = float(r["s"])
            loaded.add(f)
    return dict(
        days=days,
        contracts=contracts,
        futures=futures,
        source="Tushare trade_cal / opt_basic；新浪具体合约期权日线、期货日线结算字段 s",
    )


def register_native(root, packet):
    """Publish verified daily bars under their own WT profile, without changing a collector."""
    import shutil
    import tempfile
    from wtpy import WTSBarStruct
    from wtpy.wrapper.WtDtHelper import WtDataHelper

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", packet.stem):
        raise ValueError("样本名只允许字母、数字、短横和下划线")
    pack = json.loads(packet.read_text(encoding="utf8"))
    dest = root / "history/native" / packet.stem
    if dest.exists():
        raise ValueError("该历史行情已登记")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent.parent) as tmp:
        profile = Path(tmp) / "profile"
        common = profile / "common"
        common.mkdir(parents=True)
        for filename in ("commodities.json", "sessions.json", "holidays.json"):
            shutil.copyfile(root / "common" / filename, common / filename)
        instruments = {}
        helper = WtDataHelper()
        storage = profile / "storage/his/day/DCE"
        storage.mkdir(parents=True)
        for code, c in pack["contracts"].items():
            m = re.fullmatch(r"(M\d{4})-([CP])-(\d+)\.DCE", code)
            if not m or (c["kind"], c["strike"], c["mult"], c["future"]) != (
                m[2],
                int(m[3]),
                10,
                m[1] + ".DCE",
            ):
                raise ValueError("合约资料与具体合约不一致")
            ticker = m[1].lower() + "-" + m[2] + "-" + m[3]
            bars = [
                WTSBarStruct(
                    date=int(d),
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    vol=b["volume"],
                )
                for d, b in sorted(c["bars"].items())
                if b["volume"] > 0
            ]
            if not bars:
                continue
            instruments[ticker] = dict(
                name=ticker,
                code=ticker,
                exchg="DCE",
                product="m_o",
                expiredate=int(c["expiry"]),
                option=dict(
                    underlying=m[1].lower(),
                    optiontype=49 if m[2] == "C" else 50,
                    strikeprice=c["strike"],
                    underlyingscale=1,
                ),
            )
            if not helper.store_bars(
                str(storage / (ticker + ".dsb")), (WTSBarStruct * len(bars))(*bars), len(bars), "d"
            ):
                raise ValueError("WT 未能写入历史日线")
        if not instruments:
            raise ValueError("没有可导入的成交日线")
        (common / "contracts.json").write_text(json.dumps({"DCE": instruments}))
        hub = profile / "datahub"
        hub.mkdir()
        files = {
            k: "../common/" + n + ".json"
            for k, n in (
                ("commodity", "commodities"),
                ("contract", "contracts"),
                ("session", "sessions"),
                ("holiday", "holidays"),
            )
        }
        (hub / "catalog.json").write_text(json.dumps(dict(writer=dict(path="../storage"), basefiles=files)))
        profile.rename(dest)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--provider-config", type=Path, required=True)
    p.add_argument("--native-root", type=Path, help="同时登记到期权工作区的原生历史目录")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("codes", nargs="+")
    a = p.parse_args()
    pack = fetch(TushareHistory(a.provider_config), a.codes, a.start, a.end)
    a.output.parent.mkdir(exist_ok=True, parents=True)
    with a.output.open("x", encoding="utf8") as f:
        json.dump(pack, f, ensure_ascii=False, allow_nan=False)
    if a.native_root:
        register_native(a.native_root, a.output)


if __name__ == "__main__":
    main()
