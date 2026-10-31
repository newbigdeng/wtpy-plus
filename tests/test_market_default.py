"""The initial chart follows stored market interest, not touched files."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wtpy.WtCoreDefs import WTSBarStruct
from wtpy.wrapper.WtDtHelper import WtDataHelper
from wtpy.monitor.WorkbenchApi import WorkbenchFeed
from wtpy.monitor.MarketDataWorker import contract_summaries, read_market, _rank_cache, _history_rank_cache


class MarketDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-market-default-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = self.root / "storage"
        self.contracts = [{"code": "SHFE."+p+".2701", "exchange": "SHFE", "ticker": p+"2701"}
                          for p in ("rb", "cu", "au")]
        (self.root/"contracts.json").write_text(json.dumps({"SHFE": {
            p+"2701": {"product": p} for p in ("rb", "cu", "au")}}))
        (self.root/"config.json").write_text(json.dumps({"data": {"store": {"path": "storage"}},
            "basefiles": {"contract": "contracts.json"}}))
        _rank_cache.clear()
        _history_rank_cache.clear()

    def store(self, ticker, date, hold, volume=10):
        path = self.storage/"his/day/SHFE"/(ticker+".dsb")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (WTSBarStruct*1)()
        data[0].date = date
        data[0].open = data[0].high = data[0].low = data[0].close = 3000
        data[0].vol, data[0].hold = volume, hold
        self.assertTrue(WtDataHelper().store_bars(str(path), data, 1, "d"))
        return path

    def test_real_dsb_interest_and_day_override_mtime(self):
        rb = self.store("rb2701", 20261020, 100, 20)
        cu = self.store("cu2701", 20261019, 999999)
        self.store("au2701", 20261020, 80, 999)
        os.utime(cu, (rb.stat().st_mtime+3600, rb.stat().st_mtime+3600))
        feed = WorkbenchFeed(self.root/"history")
        self.addCleanup(feed.close)
        result = feed.market({"path": str(self.root)}, period="d")
        self.assertEqual(result["code"], "SHFE.rb.2701")
        self.assertEqual(result["bars"][-1]["open_interest"], 100)
        self.assertEqual(feed.market({"path": str(self.root)}, "SHFE.cu.2701", "d")["code"], "SHFE.cu.2701")

    def test_same_day_interest_tie_uses_volume(self):
        self.store("rb2701", 20261020, 100, 30)
        self.store("cu2701", 20261020, 100, 20)
        feed = WorkbenchFeed(self.root/"history")
        self.addCleanup(feed.close)
        self.assertEqual(feed.market({"path": str(self.root)}, period="d")["code"], "SHFE.rb.2701")

    def test_browser_date_strings_are_normalized_before_native_filtering(self):
        self.store('rb2701',20261020,100)
        feed=WorkbenchFeed(self.root/'history');self.addCleanup(feed.close)
        market=feed.market({'path':str(self.root)},'SHFE.rb.2701','d',20,False,'20261020','20261020')
        self.assertEqual([row['date'] for row in market['bars']],[20261020])

    def test_data_quality_warning_follows_the_displayed_interval(self):
        path=self.storage/'his/min1/SHFE/rb2701.dsb'
        path.parent.mkdir(parents=True)
        bars=(WTSBarStruct*2)()
        for row,stamp in zip(bars,[202610260931,202610270931]):
            row.date=stamp//10000;row.time=stamp-199000000000
            row.open=row.high=row.low=row.close=3000
        self.assertTrue(WtDataHelper().store_bars(str(path),bars,2,'m1'))
        (self.storage/'data-quality.json').write_text(json.dumps({'SHFE.rb2701':[
            dict(periods=['m1','m5'],start=202610260901,end=202610261035,message='Source gap')]}))
        feed=WorkbenchFeed(self.root/'history');self.addCleanup(feed.close)
        group={'path':str(self.root)}
        self.assertEqual(feed.market(group,'SHFE.rb.2701','m1',600,False,20261026,20261026)['data_warnings'],['Source gap'])
        self.assertEqual(feed.market(group,'SHFE.rb.2701','m1',600,False,20261027,20261027)['data_warnings'],[])

    def test_archived_real_dsb_provides_closed_reference_without_inventing_tick(self):
        # The collector has archived the day's minutes and removed realtime ticks.
        path=self.storage/'his/min1/SHFE/rb2701.dsb'
        path.parent.mkdir(parents=True)
        bars=(WTSBarStruct*2)()
        for row,stamp,price in zip(bars,[202610261459,202610261500],[3000,3001]):
            row.date=20261026;row.time=stamp-199000000000
            row.open=row.high=row.low=row.close=price
            row.vol=7;row.hold=120
        self.assertTrue(WtDataHelper().store_bars(str(path),bars,2,'m1'))
        payload=dict(storage=str(self.storage),exchange='SHFE',ticker='rb2701',
                     directory='min1',period='m1',count=600)
        result=read_market(payload)
        quote=result['quote']
        self.assertEqual(quote['price'],3001)
        self.assertEqual((quote['action_date'],quote['action_time']),(20261026,150000000))
        self.assertEqual(quote['bar_end_time'],202610261500)
        self.assertEqual(result['quote_basis'],'closed_bar')
        self.assertEqual(quote['quote_basis'],'closed_bar')
        self.assertEqual(quote['source'],'最近闭合K线参考价')
        self.assertFalse(quote['is_realtime'])
        for field in ('bid','ask','pre_close','volume','open_interest'):
            self.assertIsNone(quote[field])
        # Daily display still uses an actual minute end, never a fabricated 23:59.
        daily=read_market({**payload,'directory':'day','period':'d'})
        self.assertEqual(daily['quote']['bar_end_time'],202610261500)
        # A historical-date query must never fetch today's tick or reference.
        ticks=self.storage/'rt/ticks/SHFE/rb2701.dmb'
        ticks.parent.mkdir(parents=True);ticks.touch()
        with patch.object(WtDataHelper(),'read_dmb_ticks') as reader:
            historical=read_market({**payload,'start_date':20261026,'end_date':20261026})
            reader.assert_not_called()
        self.assertIsNone(historical['quote'])
        self.assertIsNone(historical['quote_basis'])

    def test_realtime_bar_prevents_loading_full_historical_minutes(self):
        path = self.storage/"rt/min1/SHFE/rb2701.dmb"
        path.parent.mkdir(parents=True)
        path.touch()
        import numpy as np
        from wtpy.WtDataDefs import NpTypeBar
        bars = np.zeros(1, dtype=NpTypeBar)
        bars["date"], bars["time"], bars["open_interest"] = 20261020, 202610201000-199000000000, 100
        native = type("Bars", (), {"ndarray": bars, "__len__": lambda self: len(self.ndarray)})()
        with patch("wtpy.monitor.MarketDataWorker.WtDataHelper") as helper:
            helper.return_value.read_dmb_bars.return_value = native
            result = contract_summaries({"storage": str(self.storage), "contracts": self.contracts[:1]})
            helper.return_value.read_dsb_bars.assert_not_called()
        self.assertEqual(result["summaries"][0]["open_interest"], 100)

    def test_newer_history_is_not_hidden_by_stale_realtime_and_is_reused(self):
        import numpy as np
        from wtpy.WtDataDefs import NpTypeBar
        def bars(date, hold):
            data = np.zeros(1, dtype=NpTypeBar)
            data["date"], data["time"], data["open_interest"] = date, date*10000+1000-199000000000, hold
            return type("Bars", (), {"ndarray":data, "__len__":lambda self:len(self.ndarray)})()
        realtime = self.storage/"rt/min1/SHFE/rb2701.dmb"
        history = self.storage/"his/min1/SHFE/rb2701.dsb"
        for path in (realtime, history):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        payload = {"storage":str(self.storage), "contracts":self.contracts[:1]}
        with patch("wtpy.monitor.MarketDataWorker.WtDataHelper") as helper:
            helper.return_value.read_dmb_bars.return_value = bars(20261020, 100)
            helper.return_value.read_dsb_bars.return_value = bars(20261023, 200)
            result = contract_summaries(payload)
            self.assertEqual(result["summaries"][0]["date"], 20261023)
            self.assertEqual(result["summaries"][0]["open_interest"], 200)
            _rank_cache.clear()
            contract_summaries(payload)
            self.assertEqual(helper.return_value.read_dsb_bars.call_count, 1)
            history.write_bytes(b"replacement")
            helper.return_value.read_dsb_bars.return_value = bars(20261026, 300)
            result = contract_summaries(payload)
            self.assertEqual(result["summaries"][0]["date"], 20261026)
            self.assertEqual(helper.return_value.read_dsb_bars.call_count, 2)

    def test_native_day_history_and_realtime_follow_the_latest_trading_day(self):
        import numpy as np
        from wtpy.WtDataDefs import NpTypeBar
        self.store("rb2701", 20261023, 200)
        realtime = self.storage/"rt/min1/SHFE/rb2701.dmb"
        realtime.parent.mkdir(parents=True)
        realtime.touch()
        data = np.zeros(1, dtype=NpTypeBar)
        bars = type("Bars", (), {"ndarray":data, "__len__":lambda self:len(self.ndarray)})()
        with patch.object(WtDataHelper(), "read_dmb_bars", return_value=bars):
            for date, expected in ((20261020, 200), (20261026, 300)):
                data["date"], data["time"], data["open_interest"] = date, date*10000+1000-199000000000, 300
                _rank_cache.clear()
                result = contract_summaries({"storage":str(self.storage), "contracts":self.contracts[:1]})
                self.assertEqual(result["summaries"][0]["open_interest"], expected)


if __name__ == "__main__":
    unittest.main()
