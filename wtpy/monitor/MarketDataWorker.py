"""Keep native market-file parsing in an isolated reader process."""
import json
import sys
import logging
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
except ImportError:
    pass
from wtpy.wrapper.WtDtHelper import WtDataHelper
from wtpy.monitor.WorkbenchApi import finite

def read_market(payload):
    storage=Path(payload["storage"]);exchange=payload["exchange"];ticker=payload["ticker"]
    directory=payload["directory"];period=payload["period"];count=payload["count"]
    helper=WtDataHelper()
    rows={}; available_dates=[]
    start_date=payload.get("start_date");end_date=payload.get("end_date")
    for kind,suffix,reader in (("his",".dsb",helper.read_dsb_bars),("rt",".dmb",helper.read_dmb_bars)):
        path=storage/kind/directory/exchange/(ticker+suffix)
        if not path.is_file():continue
        bars=reader(str(path))
        if bars is None:continue
        data=bars.ndarray
        if not len(data):continue
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
    tick_path=storage/"rt/ticks"/exchange/(ticker+".dmb")
    if not (start_date or end_date) and tick_path.is_file():
        ticks=helper.read_dmb_ticks(str(tick_path))
        if ticks is not None and len(ticks):
            tick=ticks.ndarray[-1];names=ticks.ndarray.dtype.names
            def number(*keys):
                key=next((k for k in keys if k in names),None)
                return float(tick[key]) if key else None
            latest={"price":number("price"),"open":number("open"),"high":number("high"),"low":number("low"),"pre_close":number("pre_close"),"volume":number("total_volume"),"open_interest":number("open_interest"),"bid":number("bid_price_0"),"ask":number("ask_price_0"),"action_date":number("action_date"),"action_time":number("action_time"),"source":"数据中台最新Tick"}
    return finite({"bars":ordered,"quote":latest,"range":{"start_date":min(available_dates),"end_date":max(available_dates)} if available_dates else {}})
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
