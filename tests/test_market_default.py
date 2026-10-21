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
from wtpy.monitor.MarketDataWorker import contract_summaries, _rank_cache


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


if __name__ == "__main__":
    unittest.main()
