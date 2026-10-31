"""Keep native market-file parsing in an isolated reader process."""
import json
import sys
import logging
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
except ImportError:
    pass
from wtpy.wrapper.WtDtHelper import WtDataHelper
from wtpy.monitor.WorkbenchApi import finite

_rank_cache={}
_history_rank_cache={}

def contract_summaries(payload):
    """Rank actual stored bars, never filesystem modification timestamps."""
    storage=Path(payload["storage"])
    contracts=payload["contracts"]
    history={}
    for item in contracts:
        for directory in ("day","min5","min1"):
            path=storage/"his"/directory/item["exchange"]/(item["ticker"]+".dsb")
            if path.is_file():
                stat=path.stat()
                history[path]=(stat.st_size,stat.st_mtime_ns)
    key=(str(storage),tuple((item["exchange"],item["ticker"]) for item in contracts),tuple(history.items()))
    cached=_rank_cache.get(key)
    if cached and time.monotonic()-cached[0]<30:
        return cached[1]
    helper=WtDataHelper()
    rows=[]
    for item in contracts:
        snapshots=[]
        # Retain only historical tail summaries. Unchanged multi-year files
        # need no repeated native reads; stale realtime cannot hide imports.
        for kind,suffix,reader,directories in (("rt",".dmb",helper.read_dmb_bars,("min1","min5")),
                                              ("his",".dsb",helper.read_dsb_bars,("day","min5","min1"))):
            for directory in directories:
                path=storage/kind/directory/item["exchange"]/(item["ticker"]+suffix)
                if kind=="his" and path not in history:continue
                if not path.is_file():continue
                cached=_history_rank_cache.get(path) if kind=="his" else None
                if cached and cached[0]==history[path]:
                    snapshot=cached[1]
                else:
                    bars=reader(str(path))
                    snapshot=None
                    if bars is not None and len(bars):
                        record=bars.ndarray[-1]
                        stamp=int(record["date"])*10000+2359 if directory=="day" else 199000000000+int(record["time"])
                        snapshot={"date":int(record["date"]),"time":stamp,
                                  "open_interest":float(record["open_interest"]),"volume":float(record["volume"])}
                    if kind=="his":
                        _history_rank_cache[path]=(history[path],snapshot)
                        if len(_history_rank_cache)>512:_history_rank_cache.pop(next(iter(_history_rank_cache)))
                if snapshot:snapshots.append(dict(snapshot,code=item["code"]))
        if not snapshots:
            path=storage/"rt/ticks"/item["exchange"]/(item["ticker"]+".dmb")
            if path.is_file():
                ticks=helper.read_dmb_ticks(str(path))
                if ticks is not None and len(ticks):
                    tick=ticks.ndarray[-1]
                    snapshots.append({"code":item["code"],"date":int(tick["trading_date"]),
                        "time":int(tick["action_date"])*10000+int(tick["action_time"])//100000,
                        "open_interest":float(tick["open_interest"]),"volume":float(tick["total_volume"])})
        if snapshots:rows.append(max(snapshots,key=lambda row:(row["date"],row["time"])))
    result={"summaries":finite(rows)}
    _rank_cache.clear()
    _rank_cache[key]=(time.monotonic(),result)
    return result

def live_quote(helper, storage, exchange, ticker):
    path = storage / 'rt/ticks' / exchange / (ticker + '.dmb')
    if not path.is_file():return None
    ticks = helper.read_dmb_ticks(str(path))
    if ticks is None or not len(ticks):return None
    tick = ticks.ndarray[-1]; names = ticks.ndarray.dtype.names
    def number(key):return float(tick[key]) if key in names else None
    fields = {'price':'price','open':'open','high':'high','low':'low','pre_close':'pre_close',
              'volume':'total_volume','open_interest':'open_interest','bid':'bid_price_0',
              'ask':'ask_price_0','action_date':'action_date','action_time':'action_time'}
    return {**{key:number(field) for key,field in fields.items()},'source':'数据中台最新Tick',
            'quote_basis':'tick','is_realtime':True}


def read_market(payload):
    if payload.get("operation")=="contract_summaries":return contract_summaries(payload)
    storage=Path(payload["storage"]);exchange=payload["exchange"];ticker=payload["ticker"]
    directory=payload["directory"];period=payload["period"];count=payload["count"]
    helper=WtDataHelper()
    if payload.get('quote_only'):
        return finite({'bars':[], 'quote':live_quote(helper,storage,exchange,ticker),'range':{}})
    rows={}; available_dates=[]; closed_bars={}
    start_date=payload.get("start_date");end_date=payload.get("end_date")
    for kind,suffix,reader in (("his",".dsb",helper.read_dsb_bars),("rt",".dmb",helper.read_dmb_bars)):
        path=storage/kind/directory/exchange/(ticker+suffix)
        if not path.is_file():continue
        bars=reader(str(path))
        if bars is None:continue
        data=bars.ndarray
        if not len(data):continue
        if kind=="his" and directory in ("min1","min5"):
            closed_bars[directory]=data[-1]
        dates=data["date"].astype("int64") if period=="d" else (199000000000+data["time"].astype("int64"))//10000
        available_dates.extend([int(dates.min()),int(dates.max())])
        mask=(dates>=start_date) if start_date else (dates>0)
        if end_date:mask &= dates<=end_date
        for record in data[mask][-count:]:
            date=int(record["date"]); raw_time=int(record["time"])
            stamp=date if period=="d" else 199000000000+raw_time
            row={"time":stamp,"date":date,"open":float(record["open"]),"high":float(record["high"]),"low":float(record["low"]),"close":float(record["close"]),"volume":float(record["volume"]),"open_interest":float(record["open_interest"])}
            rows[stamp]=row
    ordered=[rows[k] for k in sorted(rows)][-count:]
    latest=None
    if not (start_date or end_date):latest=live_quote(helper,storage,exchange,ticker)
    if latest is None and not (start_date or end_date):
        # History minute files contain closed bars. Realtime's last bar can
        # still be forming, and a daily record has no precise close timestamp.
        for reference_directory in ("min1","min5"):
            if reference_directory in closed_bars:continue
            path=storage/"his"/reference_directory/exchange/(ticker+".dsb")
            if not path.is_file():continue
            bars=helper.read_dsb_bars(str(path))
            if bars is not None and len(bars):closed_bars[reference_directory]=bars.ndarray[-1]
        if closed_bars:
            reference_directory,bar=max(closed_bars.items(),key=lambda item:(int(item[1]["time"]),item[0]=="min1"))
            stamp=199000000000+int(bar["time"])
            latest={"price":float(bar["close"]),"open":None,"high":None,"low":None,"pre_close":None,
                    "volume":None,"open_interest":None,"bid":None,"ask":None,
                    "action_date":stamp//10000,"action_time":stamp%10000*100000,
                    "source":"最近闭合K线参考价","quote_basis":"closed_bar","is_realtime":False,
                    "source_period":"m1" if reference_directory=="min1" else "m5","bar_end_time":stamp}
    quality=storage/'data-quality.json'
    warnings=[]
    if ordered and quality.is_file():
        for item in json.loads(quality.read_text()).get(exchange+'.'+ticker,[]):
            if period in item['periods'] and item['start']<=ordered[-1]['time'] and item['end']>=ordered[0]['time']:
                warnings.append(item['message'])
    return finite({"bars":ordered,"quote":latest,"quote_basis":latest.get("quote_basis") if latest else None,
                   "data_warnings":warnings,"range":{"start_date":min(available_dates),"end_date":max(available_dates)} if available_dates else {}})
def main():
    if "--serve" in sys.argv:
        WtDataHelper()
        print(json.dumps({"ready":True}),flush=True)
        for line in sys.stdin:
            try:
                result=read_market(json.loads(line))
            except Exception:
                logging.exception("Native market file read failed")
                result={"error":"行情文件读取失败"}
            print(json.dumps(result,allow_nan=False),flush=True)
    else:
        print(json.dumps(read_market(json.load(sys.stdin)),allow_nan=False))

if __name__=="__main__":
    try:main()
    except Exception:
        logging.exception("Native market file read failed")
        raise SystemExit(2)
