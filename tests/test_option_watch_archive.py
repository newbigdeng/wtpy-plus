"""The watch page may fill a hole, but may never replace WT or look ahead."""
from datetime import datetime,timedelta
from unittest.mock import patch

from wtpy.monitor import OptionWatchArchive as watch


def bar(t,price,source):
    return dict(day=t,open=price,high=price,low=price,close=price,volume=2,source=source)


def test_local_precedence_and_offline_reopen(tmp_path):
    t="2026-10-30 14:59"
    local=[bar(t,31,"WT 本地")]
    public=[bar(t,29,"东方财富"),bar("2026-10-30 15:00",30,"东方财富")]
    from contextlib import closing
    with closing(watch.database(tmp_path,"DCE","m2701-P-3100")) as db:
        with patch.object(watch,"native_rows",return_value=local),patch.object(watch,"covered",return_value=False),patch.object(watch,"remote_bars",return_value=public) as fetch:
            args=(tmp_path,db,"DCE","m2701-P-3100","m2701P3100","m1","2026-10-30")
            rows,_=watch.bars_for(*args)
            assert [(r["close"],r["source"]) for r in rows]==[(31,"WT 本地"),(30,"东方财富")]
            assert watch.bars_for(*args)[0]==rows
            assert fetch.call_count==1
        with patch.object(watch,"covered",return_value=True),patch.object(watch,"native_rows",return_value=local),patch.object(watch,"remote_bars",side_effect=AssertionError):
            assert watch.bars_for(*args)[0][0]["close"]==31


def test_five_minute_requires_full_window():
    begin=datetime(2026,10,30,21)
    rows=[bar((begin+timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M"),20+i,"WT 本地") for i in range(1,7)]
    candle,=watch.five_minute(rows)
    assert (candle["day"],candle["open"],candle["close"],candle["volume"])==("2026-10-30 21:05",21,25,10)
    assert candle["amount"] is None


def test_native_file_uses_civil_minute_and_trading_day(tmp_path):
    from wtpy import WTSBarStruct
    from wtpy.wrapper.WtDtHelper import WtDataHelper
    storage=tmp_path/"storage/his/min1/DCE"
    storage.mkdir(parents=True)
    b=WTSBarStruct(date=20261102,time=(20261030-19900000)*10000+2101,open=28.5,high=30,low=27.5,
                   close=27.5,vol=87,hold=45200,money=24345)
    helper=WtDataHelper()
    assert helper.store_bars(str(storage/"m2701-P-3100.dsb"),(WTSBarStruct*1)(b),1,"m1")
    row,=watch.native_rows(tmp_path,"DCE","m2701-P-3100","m1","2026-10-30")
    assert (row["day"],row["trading_day"],row["volume"],row["oi"])==("2026-10-30 21:01","20261102",87,45200)


def test_valuation_requires_same_clock():
    t="2026-10-30 14:59"
    meta=dict(expiry=20261207,side="看跌",strike=3100)
    a=bar(t,27,"WT 本地")
    assert not watch.valuations(meta,[a],[bar("2026-10-30 15:00",3180,"东方财富")])
    v,=watch.valuations(meta,[a],[bar(t,3180,"东方财富")])
    assert 0<v["iv"]<2 and -1<v["delta"]<0 and v["gamma"]>0 and v["hv20"] is None
