"""Rollover reminders must not turn missing or older quotes into a main contract."""
from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from wtpy.monitor.ContractRollover import planned_exit, rollover_hint, planning_hint


class ContractRolloverTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 25, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.current = dict(code="SHFE.rb.2701", exchange="SHFE", product="rb", expire_date=20270115, holiday="CHINA")
        self.next = dict(self.current, code="SHFE.rb.2705", expire_date=20270517)
        self.catalog = [self.current, self.next]

    def hint(self, rows):
        return rollover_hint(self.current, self.catalog, rows, {}, self.now)

    def test_missing_far_month_does_not_predict_next_main(self):
        result = self.hint([dict(code=self.current["code"], date=20261023, open_interest=100)])
        self.assertIsNone(result["candidate"])
        self.assertFalse(result["liquidity_crossed"])
        self.assertEqual(result["plan_deadline"], "2026-12-31")
        self.assertIn("2026-11-01", result["recommendation"])
        self.assertEqual(result['planned_candidate']['code'], self.next['code'])
        self.assertEqual(result['observation_window'], {'start':'2026-11-01','end':'2026-12-31'})

    def test_same_day_interest_crossing_changes_recommendation(self):
        result = self.hint([dict(code=self.current["code"], date=20261023, open_interest=100),
                            dict(code=self.next["code"], date=20261023, open_interest=120)])
        self.assertEqual(result["candidate"]["code"], self.next["code"])
        self.assertTrue(result["liquidity_crossed"])
        self.assertIn("截至2026-10-23", result["recommendation"])

    def test_older_far_month_is_not_compared_with_current(self):
        result = self.hint([dict(code=self.current["code"], date=20261023, open_interest=100),
                            dict(code=self.next["code"], date=20261022, open_interest=999)])
        self.assertIsNone(result["candidate"])

    def test_expired_and_other_product_cannot_win(self):
        self.catalog.extend([dict(self.current, code="SHFE.rb.2610", expire_date=20261015),
                             dict(self.next, code="SHFE.cu.2705", product="cu")])
        result = self.hint([dict(code="SHFE.rb.2610", date=20261023, open_interest=999),
                            dict(code="SHFE.cu.2705", date=20261023, open_interest=999)])
        self.assertIsNone(result["candidate"])

    def test_calendar_skips_weekends_and_holidays_but_not_unknown_years(self):
        self.assertEqual(planned_exit(dict(self.current, expire_date=20260316), {}), "2026-02-27")
        self.assertEqual(planned_exit(dict(self.current, expire_date=20260515), {"CHINA": [20260430]}), "2026-04-29")
        self.assertIsNone(planned_exit(self.next, {}))

    def test_cffex_contract_uses_its_own_expiry(self):
        self.assertEqual(planned_exit(dict(self.current, exchange="CFFEX", product='IF', expire_date=20261120), {}), "2026-11-20")
        self.assertIsNone(planned_exit(dict(self.current, exchange='CFFEX', product='T'), {}))

    def test_rb_candidate_skips_other_listed_months(self):
        self.catalog.append(dict(self.next, code='SHFE.rb.2702', expire_date=20270215))
        self.assertEqual(self.hint([])['planned_candidate']['code'], 'SHFE.rb.2705')
        self.assertIn('30手', self.hint([])['delivery_note'])

    def test_monthly_and_odd_month_products_keep_their_own_habits(self):
        for product, expected, months in [('cu','2702',list(range(1,13))),('c','2703',[1,3,5,7,9,11])]:
            current=dict(self.current, product=product, code='DCE.'+product+'.2701')
            later=[dict(current,code='DCE.'+product+'.'+month,expire_date=expiry)
                   for month,expiry in [('2702',20270215),('2703',20270315),('2705',20270517)]]
            result=planning_hint(current,[current]+later,{},self.now)
            self.assertTrue(result['planned_candidate']['code'].endswith(expected))
            self.assertEqual(result['pattern_months'],months)

    def test_index_cash_delivery_does_not_get_commodity_window(self):
        current=dict(self.current,exchange='CFFEX',product='IF')
        result=planning_hint(current,[current],{},self.now)
        self.assertIsNone(result['observation_window'])
        self.assertIn('现金交割',result['delivery_note'])


if __name__ == "__main__":
    unittest.main()
