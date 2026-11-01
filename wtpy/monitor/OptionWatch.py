"""Read-only contract search and single-contract daily charts for the option desk."""
from datetime import date, datetime
from functools import lru_cache
import json
import math
from threading import Lock, Thread
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from .AssetMarkets import OPTION, inside, instrument
from .OptionExpiryHistory import daily
from .OptionChain import save_directory


_refresh_lock=Lock()
_last_refresh={}


def refresh_directory(root, reference):
    today=datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if reference.exists() and datetime.fromtimestamp(reference.stat().st_mtime,ZoneInfo("Asia/Shanghai")).date()>=today:
        return
    with _refresh_lock:
        if time.monotonic()-_last_refresh.get(root,-float("inf"))<900:return
        _last_refresh[root]=time.monotonic()
    def update():
        try:
            with urlopen("http://dict.openctp.cn/instruments?types=option",timeout=8) as response:
                data=json.loads(response.read(48*1024*1024))
            save_directory(root,data)
        except (OSError,ValueError,KeyError,TypeError):
            pass  # Keep the last usable contract directory during provider outages.
    if reference.is_file():Thread(target=update,daemon=True).start()
    else:update()


@lru_cache(maxsize=2)
def directory(reference, native, reference_stamp, native_stamp):
    rows = {}
    if reference_stamp:
        chains = json.loads(reference.read_bytes())["chains"]
        for key, legs in chains.items():
            for leg in legs:
                rows[leg["symbol"]] = dict(leg, underlying=key)
    if native_stamp:
        from .MarketSubscriptions import _load
        for exchange, items in _load(native).items():
            for code, item in items.items():
                option = item.get("option")
                match = OPTION.fullmatch(code)
                if option and match:
                    symbol = exchange + "." + code
                    rows[symbol] = dict(symbol=symbol, side="看涨" if match[4]=="C" else "看跌",
                                        strike=float(match[5]), expiry=item.get("expiredate"),
                                        underlying=exchange+"."+option["underlying"])
    return rows


def catalog(root):
    files = [inside(root,"reference","chain.json"), inside(root,"common","contracts.json")]
    refresh_directory(root,files[0])
    return directory(*files, *(f.stat().st_mtime_ns if f.exists() else 0 for f in files))


def compact(code):
    return code.upper().replace("-", "").replace(".", "")


def provider_future(exchange, raw, expiry=None):
    if exchange=="CZCE" and len(raw)-len(raw.rstrip("0123456789"))==3:
        # The delivery year is the same as expiry or the following year.
        if not expiry:
            raise ValueError("郑商所三位月份需要合约目录中的到期日")
        product, month = raw[:-3], raw[-3:]
        year = int(str(expiry)[:4])
        delivery = year + (int(month[0])-year%10)%10
        if delivery-year>1:
            raise ValueError("合约月份与到期日不符")
        return product.upper()+str(delivery)[-2:]+month[-2:]
    return raw


@lru_cache(maxsize=96)
def chain_interest(exchange, month, bucket):
    product = month.rstrip("0123456789").lower()
    params = dict(type="futures", exchange=exchange.lower(), product=product if exchange=="CZCE" else product+"_o",
                  pinzhong=month.lower())
    url = "https://stock.finance.sina.com.cn/futures/api/openapi.php/OptionService.getOptionData?"+urlencode(params)
    try:
        with urlopen(url,timeout=4) as response:
            data=json.load(response)["result"]["data"]
        return {compact(row[-1]):int(row[5]) for side in ("up","down") for row in data.get(side,[])
                if isinstance(row,list) and len(row)>=8 and str(row[5]).isdigit()}
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return {}


def suggestions(root, prefix, futures=False):
    query=compact(prefix.strip())
    today=int(datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d"))
    rows=[r for r in catalog(root).values() if int(r["expiry"] or 0)>=today and
          (compact(r["symbol"]).startswith(query) or compact(r["symbol"].split(".")[1]).startswith(query))]
    if not rows:return dict(items=[],ranked_chain=None)
    # Pick a listed benchmark month for broad prefixes. Specific months take precedence through filtering.
    benchmarks=("DCE.m","SHFE.au","CZCE.SR","GFEX.si","INE.sc")
    def priority(row):
        key=row["underlying"]
        product=key.rstrip("0123456789")
        major=product in benchmarks
        return (benchmarks.index(product) if major else len(benchmarks),
                0 if key[-2:] in ("01","05","09") else 1, row["expiry"],key)
    preferred=min(rows,key=priority)
    if futures:
        months=sorted({r["underlying"]:r for r in rows}.values(),key=priority)
        return dict(items=[dict(code=r["underlying"],label="标的期货") for r in months[:12]],ranked_chain=None)
    key=preferred["underlying"]
    exchange,raw=key.split(".")
    month=provider_future(exchange,raw,preferred["expiry"])
    interest=chain_interest(exchange,month,int(time.time()//900))
    def oi(r):
        if r["underlying"]!=key:return None
        match=OPTION.fullmatch(r["symbol"].split(".")[1])
        return interest.get(compact(month+match[4]+match[5])) if match else None
    rows.sort(key=lambda r:(oi(r) is None,-(oi(r) or 0),priority(r),r["strike"],r["symbol"]))
    return dict(items=[dict(code=r["symbol"],label=r["side"],interest=oi(r)) for r in rows[:12]],
                ranked_chain=key if any(oi(r) is not None for r in rows[:12]) else None)


@lru_cache(maxsize=96)
def history(provider_code, bucket):
    rows={}
    for r in daily(provider_code,True):
        try:
            day=date.fromisoformat(r["d"]).isoformat()
            o,h,l,c,v=(float(r[k]) for k in ("o","h","l","c","v"))
            if not all(map(math.isfinite,(o,h,l,c,v))) or not (0<l<=min(o,c)<=max(o,c)<=h and v>=0):
                continue
            rows[day]=dict(day=day,open=o,high=h,low=l,close=c,volume=v)
        except (KeyError,TypeError,ValueError):
            continue
    return [rows[day] for day in sorted(rows)[-140:]]


def daily_view(root,symbol):
    exchange,raw=instrument("option",symbol)
    match=OPTION.fullmatch(raw)
    row=catalog(root).get(exchange+"."+raw,{})
    month=provider_future(exchange,match[1]+match[2],row.get("expiry"))
    bars=history(month+match[4]+match[5],int(time.time()//300))
    return dict(symbol=symbol,bars=bars,source="新浪财经 · 商品期权日线")
