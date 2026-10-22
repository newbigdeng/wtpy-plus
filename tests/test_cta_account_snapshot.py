"""Account sizing reads the selected native account, even without risk rules."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from wtpy.CtaContext import CtaContext
from wtpy.monitor.RiskRuntime import attach_risk, fast_account_provider


class AccountSnapshotTests(unittest.TestCase):
    def context(self, engine):
        context = CtaContext.__new__(CtaContext)
        context.__engine__ = engine
        context.__sname__ = "trend"
        return context

    def test_unbound_account_is_unavailable(self):
        self.assertFalse(self.context(SimpleNamespace()).stra_get_account_snapshot()["valid"])

    def test_no_rules_still_uses_selected_account_and_copies_snapshot(self):
        account = dict(valid=True, equity=120000, source="native")
        channels = []
        def provider(context, channel):
            channels.append(channel)
            return account
        engine = SimpleNamespace(_risk_channels={"trend": "paper"})
        with tempfile.TemporaryDirectory() as folder:
            attach_risk(engine, None, Path(folder) / "risk.jsonl", provider)
            snapshot = self.context(engine).stra_get_account_snapshot()
        self.assertEqual(channels, ["paper"])
        self.assertEqual(snapshot["equity"], 120000)
        snapshot["equity"] = 0
        self.assertEqual(account["equity"], 120000)

    def test_fast_account_adds_initial_capital_to_native_net_profit(self):
        engine = SimpleNamespace(_account_provider=fast_account_provider(100000))
        context = self.context(engine)
        context.stra_get_fund_data = lambda flag: 2300
        context.stra_get_date = lambda: 20261021
        context.stra_get_tdate = lambda: 20261021
        self.assertEqual(context.stra_get_account_snapshot()["equity"], 102300)


if __name__ == "__main__":
    unittest.main()
