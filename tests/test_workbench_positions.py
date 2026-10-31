import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from wtpy.monitor.WorkbenchApi import channel_positions, trades_with_dates, theoretical_position_summary, position_market_fields


class WorkbenchPositionsTests(unittest.TestCase):
    now=1792675620000

    def test_native_zero_sides_do_not_create_a_long_position(self):
        empty={"newvol":0,"prevol":0,"newavail":0,"preavail":0}
        self.assertEqual(channel_positions([{"code":"SHFE.rb.2701","long":empty,"short":empty}],self.now),[])

    def test_each_side_keeps_quantity_without_inventing_costs(self):
        rows=channel_positions([{"code":"SHFE.rb.2701","channel":"test",
            "long":{"newvol":1,"prevol":2,"newavail":0,"preavail":2},
            "short":{"newvol":1,"prevol":0,"newavail":1,"preavail":0}}],self.now)
        self.assertEqual([(row["long"],row["volume"],row["available"]) for row in rows],[(True,3,2),(False,1,1)])
        self.assertTrue(all(row["cost"] is None and row["profit"] is None for row in rows))

    def test_native_metrics_require_fresh_receipt_and_no_later_trade(self):
        side={"newvol":1,"prevol":0,"avg_price":3033,"position_cost":30330,"dynprofit":-10,
              "metrics_updated_at":self.now,"metrics_valid":True}
        row={"code":"SHFE.rb.2701","short":side}
        first=channel_positions([row],self.now)[0]
        self.assertEqual((first["cost"],first["cost_amount"],first["profit"]),(3033,30330,-10))
        self.assertIsNone(channel_positions([row],self.now+300001)[0]["profit"])
        side["metrics_valid"]=False
        self.assertIsNone(channel_positions([row],self.now)[0]["cost"])

    def test_opening_price_is_not_settlement_cost_and_return_uses_actual_margin(self):
        side={"newvol":1,"prevol":0,"avg_price":3033,"open_price":3020,"margin":4800,"dynprofit":130,
              "metrics_updated_at":self.now,"metrics_valid":True}
        row={"code":"SHFE.rb.2701","long":side}
        first=channel_positions([row],self.now)[0]
        self.assertEqual((first["cost"],first["open_price"],first["margin"]),(3033,3020,4800))
        self.assertAlmostEqual(first["return_on_margin_pct"],130/4800*100)
        side.pop("open_price");side["margin"]=0
        missing=channel_positions([row],self.now)[0]
        self.assertIsNone(missing["open_price"])
        self.assertIsNone(missing["return_on_margin_pct"])

    def test_last_recorded_theory_is_visible_when_risk_freshness_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/"generated/portfolio";folder.mkdir(parents=True)
            (Path(tmp)/"config.json").write_text('{}')
            (folder/"datas.json").write_text(json.dumps({"fund":{"date":20261023,"update_time":self.now-60000},
                "positions":[{"code":"SHFE.rb.2701","volume":0,"details":[]}]}))
            result=theoretical_position_summary({"path":tmp},True,self.now/1000)
            self.assertTrue(result["valid"]);self.assertEqual(result["gross_lots"],0)
            self.assertFalse(result["fresh"])
            (folder/"datas.json").write_text('{')
            self.assertFalse(theoretical_position_summary({"path":tmp},True,self.now/1000)["valid"])

    def test_quote_status_does_not_turn_an_old_price_into_a_live_quote(self):
        position={"code":"SHFE.rb.2701","channel":"mock"}
        market={"session_open":True,"quote":{"price":3030,"action_date":20261022,"action_time":212700000}}
        result=position_market_fields(position,market,{("mock",position["code"]):[{"id":"a","name":"示例"}]},self.now+120000)
        self.assertEqual(result["market_state"],"stale")
        self.assertEqual(result["strategy_owners"][0]["id"],"a")
        market["session_open"]=False
        self.assertEqual(position_market_fields(position,market,{},self.now)["market_state"],"closed")

    def test_daily_fill_dates_follow_night_and_friday_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"config.json").write_text(json.dumps({"basefiles":{"session":"sessions.json","holiday":"holidays.json"}}))
            (root/"sessions.json").write_text(json.dumps({"NIGHT":{"sections":[{"from":2100,"to":2300},{"from":900,"to":1015}]}}))
            (root/"holidays.json").write_text("{}")
            contract={"code":"SHFE.rb.2701","exchange":"SHFE","session":"NIGHT","holiday":"CHINA"}
            dates=("2026-10-22T21:27:00","2026-10-23T21:27:00","2026-10-23T09:03:00","2026-10-23T08:00:00")
            trades=[{"code":contract["code"],"time":int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()*1000),"action":"OS","price":3033} for day in dates]
            output=trades_with_dates({"path":tmp},trades,[contract])
            self.assertEqual([row["trading_date"] for row in output],[20261023,20261026,20261023,None])
            self.assertEqual(output[0]["time"],trades[0]["time"])
            self.assertEqual(output[0]["price"],3033)
            self.assertNotIn("trading_date",trades[0])


if __name__=="__main__":
    unittest.main()
