import unittest

from wtpy.monitor.OptionExpiryReplay import replay


class ExpiryReplayTest(unittest.TestCase):
    def setUp(self):
        self.old, self.new = "M2501-C-3000.DCE", "M2505-C-3000.DCE"
        days = ["20241213", "20241216", "20241217", "20241218"]
        self.data = dict(source="synthetic", days=days, contracts={}, futures={})
        for code, future, expiry, prices in [
            (self.old, "M2501.DCE", days[2], [8, 9, 10]),
            (self.new, "M2505.DCE", "20250416", [11] * 4),
        ]:
            bars = {d: dict(open=p, close=p, volume=5) for d, p in zip(days, prices)}
            self.data["contracts"][code] = dict(
                future=future, kind="C", strike=3000, expiry=expiry, mult=10, bars=bars
            )
        for d, p in zip(days, [3004, 3008, 3010, 3007]):
            self.data["futures"][d] = {"M2501.DCE": p, "M2505.DCE": 3011}

    def run_case(self, **kwargs):
        return replay(self.data, old=self.old, new=self.new, **kwargs)

    def test_mirrored_accounts_pay_fees_but_have_opposite_exposure(self):
        for side in (1, -1):
            report = self.run_case(lots=side * 2)
            final = report["final"]
            self.assertEqual(final["options"], {self.old: 0, self.new: side})
            self.assertEqual(final["futures"], {"M2501.DCE": side})
            self.assertEqual(final["cash"], 100000 - side * 110 - 5)
            self.assertEqual(final["equity"], 99995)
            self.assertEqual(sum(e["fee"] for e in report["events"]), 5)

    def test_failed_new_leg_cannot_undo_old_close(self):
        self.data["contracts"][self.new]["bars"].pop("20241216")
        report = self.run_case()
        self.assertEqual(report["final"]["options"][self.new], 0)
        self.assertEqual(report["final"]["futures"], {"M2501.DCE": 1})

    def test_explicit_partial_assignment_releases_remainder(self):
        report = self.run_case(
            lots=-3, declarations=[dict(code=self.old, day="20241217", action="assign", qty=1)]
        )
        self.assertEqual(report["final"]["futures"], {"M2501.DCE": -1})
        self.assertIn("到期未指派", [e["action"] for e in report["events"]])

    def test_missing_settlement_cannot_be_replaced_by_option_close(self):
        self.data["futures"]["20241217"].pop("M2501.DCE")
        with self.assertRaisesRegex(ValueError, "结算价"):
            self.run_case()
