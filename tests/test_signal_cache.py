import datetime
from pathlib import Path
import tempfile
import unittest

from wtpy.monitor.DataMgr import DataMgr


class SignalCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="wt-signals-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/"generated/outputs/target/signals.csv"
        self.path.parent.mkdir(parents=True)
        self.manager = DataMgr.__new__(DataMgr)
        self.manager.__config__ = {"groups": {"group": {"path": temp.name}}}
        self.actual_trades, self.theory_trades = ["broker-fill"], ["theory-fill"]
        self.manager.__grp_cache__ = {"group": {
            "strategies": ["target"], "cachetime": datetime.datetime.now(),
            "chantrades": self.actual_trades, "trades": self.theory_trades}}

    def write(self, targets):
        rows = ["code,target,sigprice,gentime,tag\n"]
        rows.extend("SHFE.rb.2701,%s,3300,202610230900,test\n" % value for value in targets)
        self.path.write_text("".join(rows), encoding="utf-8")

    def signals(self):
        return self.manager.get_signals("group", "target")

    def test_truncated_signals_drop_old_rows_and_accept_new_rows(self):
        self.write([1, 2])
        self.assertEqual([row["target"] for row in self.signals()], [1, 2])
        self.write([])
        self.assertEqual(self.signals(), [])
        self.write([-1])
        self.assertEqual([row["target"] for row in self.signals()], [-1])

    def test_explicit_reset_handles_same_length_rewrite_and_preserves_trade_caches(self):
        self.write([1])
        self.signals()
        self.write([-1])
        self.manager.invalidate_strategy_signals("group", "target", "first-reset")
        self.assertEqual([row["target"] for row in self.signals()], [-1])
        cache = self.manager.__grp_cache__["group"]
        signal_cache = cache["signals"]["target"]
        self.manager.invalidate_strategy_signals("group", "target", "first-reset")
        self.assertIs(cache["signals"]["target"], signal_cache)
        self.assertIs(cache["chantrades"], self.actual_trades)
        self.assertIs(cache["trades"], self.theory_trades)
        self.write([2])
        self.manager.invalidate_strategy_signals("group", "target", "next-reset")
        self.assertEqual([row["target"] for row in self.signals()], [2])


if __name__ == "__main__":
    unittest.main()
