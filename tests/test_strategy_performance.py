import json
from pathlib import Path
import tempfile
import unittest

from wtpy.monitor.StrategyPerformance import strategy_funds


class StrategyPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root/'generated/outputs/example'
        self.output.mkdir(parents=True)
        (self.root/'generated/stradata').mkdir()
        self.group = {'path':str(self.root)}
        self.write('config.json', {'basefiles':{'commodity':'commodities.json','session':'sessions.json','holiday':'holidays.json'}})
        self.write('commodities.json', {'SHFE':{'rb':{'session':'night','holiday':'CHINA'}}})
        self.write('sessions.json', {'night':{'sections':[{'from':2100,'to':2300},{'from':900,'to':1015},{'from':1330,'to':1501}]}})
        self.write('holidays.json', {'CHINA':['20261001','20261002','20261005','20261006','20261007']})
        self.trades = 'code,time,direct,action,price,qty,tag,fee,barno\n'
        self.closes = 'code,direct,opentime,openprice,closetime,closeprice,qty,profit,totalprofit\n'
        self.profit = self.fee = 0

    def write(self, name, value):
        (self.root/name).write_text(json.dumps(value), encoding='utf-8')

    def roundtrip(self, start, end, profit):
        self.trades += f'SHFE.rb.2701,{start},LONG,OPEN,3000,1,,2,1\nSHFE.rb.2701,{end},LONG,CLOSE,3004,1,,2,2\n'
        self.profit += profit
        self.fee += 4
        self.closes += f'SHFE.rb.2701,LONG,{start},3000,{end},3004,1,{profit},{self.profit}\n'

    def read(self, day=20261027, floating=0):
        (self.output/'trades.csv').write_text(self.trades)
        (self.output/'closes.csv').write_text(self.closes)
        self.write('generated/stradata/example.json', {'fund':{'tdate':day,'total_profit':self.profit,'total_fees':self.fee,'total_dynprofit':floating}})
        return strategy_funds(self.group, 'example')

    def test_night_and_next_day_share_trading_day_without_old_loss(self):
        self.roundtrip(202610231400,202610231410,-110)
        self.roundtrip(202610262111,202610262118,40)
        self.roundtrip(202610271351,202610271359,40)
        rows = self.read()
        self.assertEqual([row['date'] for row in rows], [20261023,20261027])
        self.assertEqual(rows[-1]['profit'],80)
        self.assertEqual(rows[-1]['thisfee'],8)
        self.assertEqual(rows[-1]['addition'],72)
        self.assertEqual(rows[-1]['dynbalance'],-42)
        self.assertEqual(rows[-1]['performance_kind'],'strategy_theoretical')

    def test_missing_ledger_cannot_make_cumulative_loss_a_daily_loss(self):
        self.profit, self.fee = -30,24
        row = self.read()[0]
        self.assertEqual(row['dynbalance'],-54)
        self.assertIsNone(row['addition'])

    def test_closing_bar_at_session_end_keeps_its_trading_day(self):
        self.write('sessions.json', {'night':{'sections':[{'from':1330,'to':1500}]}})
        self.roundtrip(202610271442,202610271500,40)
        self.assertEqual(self.read()[-1]['addition'],36)

    def test_carried_position_without_previous_settlement_has_no_daily_mark(self):
        self.roundtrip(202610261400,202610271400,40)
        row = self.read()[-1]
        self.assertEqual(row['closeprofit'],40)
        self.assertIsNone(row['addition'])

    def test_native_prior_settlement_provides_carried_mark(self):
        self.roundtrip(202610261400,202610271400,40)
        (self.output/'funds.csv').write_text('date,closeprofit,positionprofit,dynbalance,fee\n20261026,0,10,8,2\n20261027,30,0,26,4\n')
        row = self.read()[-1]
        self.assertEqual(row['dynbalance'],36)
        self.assertEqual(row['addition'],28)
        self.assertEqual(row['daily_basis'],'native_settlement_difference')

    def test_friday_night_belongs_to_monday_and_holiday_eve_is_unassigned(self):
        self.roundtrip(202610232111,202610232118,40)
        self.assertEqual(self.read(day=20261026)[-1]['addition'],36)
        self.roundtrip(202609302111,202609302118,40)
        self.assertIsNone(self.read(day=20261026)[-1]['addition'])


if __name__ == '__main__':
    unittest.main()
