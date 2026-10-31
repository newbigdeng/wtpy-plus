import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote

from wtpy.monitor.TradeRiskApi import trade_risk_snapshot
from wtpy.monitor.WorkbenchApi import WorkbenchFeed, capital_performance


class AccountEquityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-equity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "group"
        self.folder.mkdir()
        self.feed = WorkbenchFeed(self.root / "history")
        self.now = 1792464000000
        identity=self.folder/"config.json"
        identity.write_text(json.dumps({"traders":[{"id":"mock","broker":"test","user":"account-a"}]}))
        os.utime(identity, ((self.now-7200000)/1000,)*2)
        self.path = self.folder / "generated/traders/mock/rtdata.json"
        self.path.parent.mkdir(parents=True)

    def snapshot(self, timestamp=None, equity_missing=False):
        fund = {"balance": 100000, "dynprofit": None if equity_missing else 2500,
                "available": 90000, "margin": 12500, "trading_date": 20261020}
        if timestamp is not None:
            fund["updated_at"] = timestamp
        self.path.write_text(json.dumps({"funds": {"CNY": fund}}))
        self.path.with_name("rtdata.json.status.json").write_text(json.dumps(
            {"connected": True, "ready": True, "trading_date": 20261020}))
        return trade_risk_snapshot({"path": str(self.folder)}, ["mock"], now=self.now, running=True)["accounts"]

    def sample(self, accounts):
        return self.feed.account_series("group", accounts, self.folder)[0]

    def test_native_equity_persists_and_same_receipt_is_not_resampled(self):
        first = self.sample(self.snapshot(self.now))
        self.assertEqual(first["equity"], 102500)
        self.assertTrue(first["valid"])
        self.assertEqual(first["history"], [{"time": self.now, "equity": 102500,
                                             "available": 90000, "margin": 12500}])
        # Rewriting the file changes mtime, but it is still the same receipt.
        repeated = self.sample(self.snapshot(self.now))
        self.assertEqual(len(repeated["history"]), 1)
        restarted = WorkbenchFeed(self.feed.history_root)
        accounts = self.snapshot(self.now + 1000)
        restarted.account_series("group", accounts, self.folder)
        self.assertEqual(len(accounts[0]["history"]), 2)

    def test_old_or_missing_equity_cannot_generate_new_points(self):
        self.sample(self.snapshot(self.now))
        older = self.sample(self.snapshot(self.now - 1000))
        self.assertEqual(len(older["history"]), 1)
        self.assertEqual(len(self.sample(self.snapshot())["history"]), 1)
        self.assertEqual(len(self.sample(self.snapshot(self.now + 1000, equity_missing=True))["history"]), 1)
        self.assertEqual(len(self.sample(self.snapshot(self.now + 2000))["history"]), 1)

    def test_stale_native_receipt_is_historical_not_current(self):
        account = self.sample(self.snapshot(self.now - 3600000))
        self.assertFalse(account["valid"])
        self.assertTrue(account["stale"])
        self.assertEqual(account["reason"], "柜台快照过期")
        self.assertEqual(account["history"][0]["time"], self.now - 3600000)

    def test_legacy_balance_points_are_preserved_outside_equity_series(self):
        target = self.feed.history_root / "group" / (quote(str(self.folder.resolve()), safe="") + ".json")
        target.parent.mkdir(parents=True)
        legacy = {"mock:CNY": [{"time": self.now - 1000, "equity": 100000}]}
        target.write_text(json.dumps(legacy))
        account = self.sample(self.snapshot(self.now))
        self.assertEqual(len(account["history"]), 1)
        self.assertEqual(account["history"][0]["equity"], 102500)
        self.assertEqual(account["legacy_balance_samples"], 1)
        self.assertEqual(json.loads(target.with_suffix(".accounts-v3.json").read_text())["legacy_balance"], legacy)

    def test_explicit_session_reset_starts_new_segment_without_erasing_history(self):
        self.sample(self.snapshot(self.now))
        target = self.feed.history_root / "group" / (quote(str(self.folder.resolve()), safe="") + ".json")
        event = {"time": self.now + 1000, "type": "simnow_reset",
                 "reason": "夜盘重置后的首笔已保存回执", "requested_capital": 10000}
        target.with_suffix(".capital-events.json").write_text(json.dumps({"mock:CNY": [event]}))
        # Both receipts belong to the same trading day. Resetting capital is
        # independent of the calendar date and of the broker trading date.
        accounts = self.snapshot(self.now + 1000)
        accounts[0]["equity"] = 9984
        result = self.sample(accounts)
        self.assertEqual([row["equity"] for row in result["history"]], [102500, 9984])
        self.assertEqual([row["equity"] for row in result["current_history"]], [9984])
        self.assertEqual(result["history_before_reset_samples"], 1)
        self.assertEqual(result["capital_events"], [event])
        restarted = WorkbenchFeed(self.feed.history_root)
        repeated = self.snapshot(self.now + 1000)
        restarted.account_series("group", repeated, self.folder)
        self.assertEqual(repeated[0]["current_history"], result["current_history"])

    def test_loss_without_explicit_capital_event_does_not_reset_history(self):
        self.sample(self.snapshot(self.now))
        accounts = self.snapshot(self.now + 1000)
        accounts[0]["equity"] = 100
        result = self.sample(accounts)
        self.assertEqual(result["current_history"], result["history"])
        self.assertEqual(result["history_before_reset_samples"], 0)
        self.assertEqual(result["capital_events"], [])

    def test_total_profit_uses_confirmed_capital_not_old_twenty_million_equity(self):
        event={"type":"simnow_reset","time":self.now,"confirmed_at":self.now+1,"requested_capital":10000}
        account={"valid":True,"equity":9957.833,"updated_at":self.now+2,"currency":"CNY","deposit":0,"withdraw":0,"timestamp_source":"柜台查询回执时间"}
        result=capital_performance(account,[event])
        self.assertTrue(result["valid"]);self.assertAlmostEqual(result["pnl"],-42.167)
        self.assertAlmostEqual(result["pnl_pct"],-.42167)
        self.assertFalse(capital_performance(account,[])["valid"])
        account["deposit"]=2000
        self.assertFalse(capital_performance(account,[event])["valid"])
        account["deposit"]=0;account["valid"]=False
        historical=capital_performance(account,[event])
        self.assertTrue(historical["valid"]);self.assertFalse(historical["fresh"])
        account["timestamp_source"]="文件时间（旧快照）"
        self.assertFalse(capital_performance(account,[event])["valid"])


if __name__ == "__main__":
    unittest.main()
