import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote

from wtpy.monitor.TradeRiskApi import trade_risk_snapshot
from wtpy.monitor.WorkbenchApi import WorkbenchFeed


class AccountEquityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-equity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "group"
        self.folder.mkdir()
        self.feed = WorkbenchFeed(self.root / "history")
        self.now = 1792464000000
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
        self.assertEqual(json.loads(target.read_text())["legacy_balance"], legacy)


if __name__ == "__main__":
    unittest.main()
