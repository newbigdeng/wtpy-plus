"""Synthetic broker receipts verify account statistics without a trading runtime."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import json
import tempfile
import unittest
from urllib.parse import quote

spec = importlib.util.spec_from_file_location("account_analytics", Path(__file__).parents[1]/"wtpy/monitor/AccountAnalytics.py")
analytics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analytics)
account_analytics = analytics.account_analytics

NOW = 1793062800000


def point(stamp=NOW, day=20261027, equity=10100, prebalance=10000, **changes):
    return {"time":stamp,"trading_date":day,"equity":equity,"prebalance":prebalance,
            "deposit":0,"withdraw":0,"fee":5,**changes}


def account(points=(), **changes):
    return {"channel":"sim","currency":"CNY","history":list(points),"capital_events":[],**changes}


def result(points=(), **kwargs):
    return account_analytics([account(points)],now=NOW+100000000,**kwargs)["accounts"][0]


class AccountAnalyticsTests(unittest.TestCase):
    def test_old_equity_points_never_become_daily_profit(self):
        value=result([{"time":NOW-1000,"equity":20000000},{"time":NOW,"equity":10000}])
        self.assertEqual(len(value["points"]),2)
        self.assertEqual(value["daily"],[])
        self.assertTrue(all(row["pnl"] is None for row in value["points"]))
        self.assertIsNone(value["summary"]["pnl"])
        self.assertIsNone(value["summary"]["max_drawdown"])
        self.assertEqual(value["coverage"]["legacy_samples_without_trading_date"],2)

    def test_night_session_uses_broker_day_not_clock_calendar(self):
        # Monday evening belongs to the broker's Tuesday trading day.
        value=result([point(stamp=1793019600000,day=20261027)])
        self.assertEqual(value["daily"][0]["trading_date"],20261027)
        self.assertEqual(value["daily"][0]["pnl"],100)
        self.assertEqual(value["daily"][0]["return_pct"],1)

    def test_daily_uses_latest_cumulative_receipt_and_does_not_deduct_fee_twice(self):
        value=result([point(stamp=NOW-1000,equity=10100),point(equity=10200,fee=30)])
        self.assertEqual(len(value["daily"]),1)
        self.assertEqual(value["daily"][0]["pnl"],200)
        self.assertEqual(value["daily"][0]["fee"],30)
        self.assertEqual(value["summary"]["pnl"],200)
        self.assertFalse(value["daily"][0]["complete"])

    def test_deposit_and_withdrawal_are_not_profit_or_return(self):
        for receipt in (point(equity=15100,deposit=5000),point(equity=8100,withdraw=2000)):
            value=result([receipt])
            self.assertEqual(value["daily"][0]["pnl"],100)
            self.assertIsNone(value["daily"][0]["return_pct"])
            self.assertIsNone(value["monthly"][0]["return_pct"])
            self.assertIsNone(value["summary"]["max_drawdown_pct"])
            self.assertEqual(len(value["cash_flows"]),1)

    def test_missing_cash_fields_or_prebalance_stays_unknown(self):
        for field in ("prebalance","deposit","withdraw"):
            receipt=point();receipt.pop(field)
            value=result([receipt])
            self.assertIsNone(value["daily"][0]["pnl"])
            self.assertIsNone(value["points"][0]["pnl"])
            self.assertIsNone(value["summary"]["pnl"])

    def test_month_is_observed_days_and_unknown_day_is_not_zero(self):
        value=result([point(day=20261026,stamp=NOW-86400000),point(day=20261028,stamp=NOW+86400000,equity=10302,prebalance=10100)])
        self.assertEqual([row["trading_date"] for row in value["daily"]],[20261026,20261028])
        month=value["monthly"][0]
        self.assertEqual(month["observed_days"],2)
        self.assertEqual(month["pnl"],302)
        self.assertAlmostEqual(month["return_pct"],3.02)
        self.assertFalse(month["complete"])
        self.assertFalse(value["coverage"]["missing_days_filled"])

    def test_range_is_broker_dates_and_cumulative_curve_restarts_at_range(self):
        value=result([point(day=20261026,stamp=NOW-86400000),point(day=20261028,stamp=NOW+86400000,equity=10302,prebalance=10100)],
                     start_date="20261028",end_date="20261028")
        self.assertEqual(len(value["points"]),1)
        self.assertEqual(value["points"][0]["pnl"],202)
        self.assertEqual(value["summary"]["pnl"],202)
        self.assertAlmostEqual(value["points"][0]["return_pct"],2)

    def test_reset_segments_prevent_old_capital_from_creating_drawdown(self):
        event={"time":NOW,"type":"simnow_reset","requested_capital":10000,"confirmed_at":NOW+1}
        row=account([point(stamp=NOW-1000,equity=20000000,prebalance=20000000),point(stamp=NOW+1,equity=10010)],capital_events=[event])
        value=account_analytics([row],segment_id="current",now=NOW+10000)["accounts"][0]
        self.assertEqual(value["selected_segment_id"],"capital-"+str(NOW))
        self.assertEqual(len(value["segments"]),2)
        self.assertEqual(len(value["points"]),1)
        self.assertEqual(value["summary"]["pnl"],10)
        self.assertEqual(value["summary"]["max_drawdown"],0)

    def test_reset_with_stale_broker_baseline_is_not_a_trading_loss(self):
        event={"time":NOW,"type":"simnow_reset","requested_capital":10000,"confirmed_at":NOW+1}
        row=account([point(stamp=NOW+1,equity=10000,prebalance=20000000)],capital_events=[event])
        value=account_analytics([row],segment_id="current",now=NOW+10000)["accounts"][0]
        self.assertIsNone(value["daily"][0]["pnl"])
        self.assertIsNone(value["summary"]["max_drawdown"])

    def test_old_reset_without_trading_day_does_not_block_new_native_day(self):
        reset={"type":"simnow_reset","time":1792677361124,"confirmed_at":1792717229501,
               "requested_capital":10000}
        stamp=1792998360000
        row=account([{"time":1792717229501,"equity":10000},
                     point(stamp=stamp,day=20261026,equity=9830.67,prebalance=9825.67)],
                    capital_events=[reset])
        value=account_analytics([row],segment_id="current",now=stamp+10000)["accounts"][0]
        self.assertEqual(len(value["daily"]),1)
        self.assertEqual(value["daily"][0]["trading_date"],20261026)
        self.assertAlmostEqual(value["daily"][0]["pnl"],5)
        self.assertAlmostEqual(value["daily"][0]["return_pct"],5/9825.67*100)
        self.assertIsNone(value["points"][0]["pnl"])

    def test_drawdown_comes_from_sampled_net_profit_not_cash_movements(self):
        value=result([point(stamp=NOW-2000,equity=10100),point(stamp=NOW-1000,equity=9900),point(equity=10150)])
        self.assertEqual(value["summary"]["max_drawdown"],200)
        self.assertAlmostEqual(value["summary"]["max_drawdown_pct"],200/10100*100)
        self.assertEqual(value["summary"]["pnl"],150)
        cash=result([point(stamp=NOW-1000,equity=10100),point(equity=7900,withdraw=2000)])
        self.assertEqual(cash["summary"]["max_drawdown"],200)
        self.assertIsNone(cash["summary"]["max_drawdown_pct"])

    def test_daily_receipts_keep_older_actual_observations_after_curve_retention(self):
        row=account([point()],daily_receipts=[point(stamp=NOW-86400000,day=20261026)])
        value=account_analytics([row],now=NOW+10000)["accounts"][0]
        self.assertEqual(value["coverage"]["stored_samples"],1)
        self.assertEqual(value["coverage"]["samples"],2)
        self.assertEqual(value["monthly"][0]["observed_days"],2)
        self.assertEqual(value["summary"]["pnl"],200)

    def test_same_authentic_receipt_can_enrich_view_without_rewriting_old_point(self):
        old={"time":NOW,"equity":10100,"available":9000,"margin":1000}
        row=account([old],daily_receipts=[point()])
        value=account_analytics([row],now=NOW+10000)["accounts"][0]
        self.assertEqual(value["points"][0]["pnl"],100)
        self.assertEqual(row["history"],[old])
        self.assertNotIn("trading_date",old)
        self.assertEqual(value["coverage"]["legacy_samples_without_trading_date"],1)

    def test_accounts_and_currencies_are_not_merged(self):
        rows=[account([point()]),account([point(equity=5050,prebalance=5000)],channel="other",currency="USD")]
        value=account_analytics(rows,now=NOW+10000)
        self.assertEqual([row["account_key"] for row in value["accounts"]],["sim:CNY","other:USD"])
        self.assertEqual([row["summary"]["pnl"] for row in value["accounts"]],[100,50])
        self.assertNotIn("summary",value)

    def test_daily_storage_retains_latest_per_day_and_phase_without_rewriting_curve(self):
        old={"time":NOW-1000,"equity":20000000}
        book={"history":[deepcopy(old)]}
        events=[{"time":NOW,"type":"simnow_reset"}]
        self.assertTrue(analytics.update_daily_receipts(book,point(),events))
        self.assertFalse(analytics.update_daily_receipts(book,point(),events))
        self.assertTrue(analytics.update_daily_receipts(book,point(stamp=NOW+1000,equity=10200),events))
        self.assertEqual(len(book["daily_receipts"]),1)
        self.assertEqual(book["daily_receipts"][0]["equity"],10200)
        self.assertEqual(book["history"],[old])

    def test_new_sample_only_preserves_present_native_fields(self):
        row={"updated_at":NOW,"equity":10000,"available":9000,"margin":1000,
             "trading_date":20261027,"prebalance":10000,"deposit":0,"withdraw":0,"fee":None}
        value=analytics.account_point(row)
        self.assertEqual(value["trading_date"],20261027)
        self.assertEqual(value["deposit"],0)
        self.assertNotIn("fee",value)

    def test_invalid_range_is_rejected(self):
        with self.assertRaises(ValueError):
            result([point()],start_date=20261028,end_date=20261027)
        with self.assertRaises(ValueError):
            result([point()],start_date="20260230")


class AccountReceiptStoreTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="account-receipts-")
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.group=self.root/"profile";self.group.mkdir()
        self.history=self.root/"history"
        self.legacy=self.history/"g"/(quote(str(self.group.resolve()),safe="")+".json")
        self.legacy.parent.mkdir(parents=True)
        self.target=self.legacy.with_suffix(".account-receipts-v1.json")
        self.core=self.legacy.with_suffix(".accounts-v3.json")
        self.book_key=":".join(quote(part,safe="") for part in ("sim","CNY","broker","account-a"))
        self.identities={"sim":(["broker","account-a"],NOW-10000)}

    def receipt(self,stamp=NOW,**changes):
        return {"channel":"sim","currency":"CNY","updated_at":stamp,"equity":10100,"prebalance":10000,
                "trading_date":20261027,"deposit":0,"withdraw":0,"timestamp_source":"柜台查询回执时间",
                "valid":False,"stale":True,**changes}

    def record(self,rows,identities=None):
        return analytics._record_receipts("g",rows,self.group,self.history,identities or self.identities,
                                          max([NOW]+[row["updated_at"] for row in rows])+100000)

    def test_sidecar_records_native_fields_but_never_writes_core_history(self):
        old={"time":NOW,"equity":10100,"available":9000,"margin":1000}
        self.core.write_text(json.dumps({"schema":3,"books":{self.book_key:{"history":[old],"capital_events":[]}}}))
        original=self.core.read_bytes()
        rows=self.record([self.receipt()])
        self.assertEqual(self.core.read_bytes(),original)
        self.assertEqual(len(rows[0]["history"]),1)
        self.assertEqual(rows[0]["history"][0]["prebalance"],10000)
        self.assertFalse(rows[0]["valid"])
        self.assertTrue(rows[0]["stale"])
        self.assertEqual(rows[0]["daily_receipts"][0]["trading_date"],20261027)
        saved=self.target.read_bytes()
        self.record([self.receipt()])
        self.assertEqual(self.target.read_bytes(),saved)

    def test_old_core_writer_cannot_erase_companion_day_history(self):
        self.record([self.receipt()])
        self.core.write_text(json.dumps({"schema":3,"books":{self.book_key:{"history":[],"capital_events":[]}}}))
        rows=self.record([self.receipt(NOW+86400000,trading_date=20261028,equity=10200,prebalance=10100)])
        self.assertEqual(len(rows[0]["daily_receipts"]),2)
        self.assertEqual([row["trading_date"] for row in rows[0]["daily_receipts"]],[20261027,20261028])
        self.assertEqual(len(json.loads(self.core.read_text())["books"][self.book_key]["history"]),0)

    def test_switching_accounts_rejects_old_receipt_and_other_accounts_reset(self):
        self.record([self.receipt()])
        foreign={"time":NOW+1000,"type":"simnow_reset","confirmed_at":NOW+1000,"requested_capital":10000}
        self.legacy.with_suffix(".capital-events.json").write_text(json.dumps({"sim:CNY":[foreign]}))
        identities={"sim":(["broker","account-b"],NOW+2000)}
        rejected=self.record([self.receipt(NOW+1500)],identities)[0]
        self.assertFalse(rejected["analytics_identity_valid"])
        self.assertEqual(rejected["history"],[])
        self.assertEqual(rejected["capital_events"],[])
        rows=self.record([self.receipt(NOW+3000,equity=50000,prebalance=50000)],identities)
        self.assertEqual(rows[0]["history"][0]["equity"],50000)
        self.assertEqual(len(json.loads(self.target.read_text())["books"]),2)
        self.assertEqual(account_analytics(rows,now=NOW+100000)["accounts"][0]["summary"]["pnl"],0)

    def test_capital_reset_keeps_separate_daily_receipts_for_same_broker_day(self):
        self.record([self.receipt()])
        event={"time":NOW+1000,"type":"simnow_reset","confirmed_at":NOW+1000,"requested_capital":5000}
        self.legacy.with_suffix(".capital-events.json").write_text(json.dumps({"sim:CNY":[event]}))
        rows=self.record([self.receipt(NOW+2000,equity=5020,prebalance=5000)])
        self.assertEqual(len(rows[0]["daily_receipts"]),2)
        value=account_analytics(rows,segment_id="current",now=NOW+100000)["accounts"][0]
        self.assertEqual(value["summary"]["pnl"],20)
        self.assertEqual(value["summary"]["max_drawdown"],0)

    def test_corrupt_companion_is_preserved_instead_of_silently_reset(self):
        self.target.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            self.record([self.receipt()])
        self.assertEqual(self.target.read_text(),"{broken")


if __name__ == "__main__":
    unittest.main()
