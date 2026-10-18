"""Config checks and isolated native replay; no broker connection is created."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

import wtpy
from wtpy.monitor.PlusBacktest import PlusBacktestManager
from wtpy.monitor.PlusExecution import effective_execution


class ExecutionConfigTests(unittest.TestCase):
    def test_real_native_names_and_curve_snapshot(self):
        for kind, name in [('minimpact', 'WtMinImpactExeUnit'), ('twap', 'WtTWapExeUnit'), ('vwap', 'WtVWapExeUnit')]:
            value = {'id': kind}
            if kind == 'vwap':
                value['volume_weights'] = [1, 2, 4, 2, 1]
            snapshot = effective_execution('CTA', 202610120900, 202610120905, value)
            self.assertEqual(snapshot['native'], 'WtExeFact.' + name)
            if kind == 'vwap':
                self.assertEqual(snapshot['params']['volume_weights'], value['volume_weights'])

    def test_unknown_modules_and_invalid_parameters_are_rejected(self):
        for value in [{'id':'arbitrary.module'}, {'id':'twap','params':{'total_times':0}},
                      {'id':'twap','params':{'begin_time':960}}, {'id':'vwap'},
                      {'id':'vwap','volume_weights':[0,0]}, {'id':'minimpact','params':{'module':'evil'}}, {'id':'minimpact','params':{'byrate':True}}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                effective_execution('CTA', 202610120900, 202610120905, value)
        with self.assertRaisesRegex(ValueError, '直接委托'):
            effective_execution('HFT', 202610120900, 202610120905, {'id':'twap'})


NATIVE = os.environ.get('WT_PLUS_NATIVE_DIR', '')
FEES = {'fees':{'byvolume':True,'open':2,'close':3,'closetoday':5,'minfee':0},
        'marginrate':.1,'slippage_ticks':2}
HFT = '''from wtpy import BaseHftStrategy
class TestStrategy(BaseHftStrategy):
 def __init__(self,name,code,**kwargs):
  super().__init__(name);self.code=code;self.state=0;self.opened=0
 def on_init(self,ctx):ctx.stra_sub_ticks(self.code)
 def on_tick(self,ctx,code,tick):
  if self.state==0:
   self.state=1;ctx.stra_buy(self.code,3400,3,'open')
  elif self.state==1 and self.opened==3:
   self.state=2;ctx.stra_sell(self.code,3200,3,'close')
 def on_trade(self,ctx,localid,code,isBuy,qty,price,tag):
  if isBuy:self.opened+=qty
'''
CTA = '''from wtpy import BaseCtaStrategy
class TestStrategy(BaseCtaStrategy):
 def __init__(self,name,code,**kwargs):
  super().__init__(name);self.code=code;self.fired=False
 def on_init(self,ctx):ctx.stra_sub_ticks(self.code)
 def on_tick(self,ctx,code,tick):
  if not self.fired:
   self.fired=True;ctx.stra_set_position(self.code,10)
'''


@unittest.skipUnless(NATIVE and (Path(NATIVE)/'libWtPorter.so').is_file(),
                     'Set WT_PLUS_NATIVE_DIR to the freshly built Plus native directory')
class NativeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wt-plus-test-')
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        package = Path(wtpy.__file__).resolve().parent.parent
        (self.workspace/'src').mkdir()
        (self.workspace/'src/wtpy').symlink_to(package, target_is_directory=True)
        (self.workspace/'artifacts').mkdir()
        (self.workspace/'artifacts/plus-native').symlink_to(Path(NATIVE).resolve(), target_is_directory=True)
        base = self.workspace/'validation/replay/common'
        base.mkdir(parents=True)
        session = {'name':'Synthetic day session','offset':0,'auction':{'from':859,'to':900},
                   'sections':[{'from':900,'to':1130},{'from':1300,'to':1515}]}
        documents = {
            'commodities.json':{'SHFE':{'rb':{'covermode':1,'pricemode':1,'category':1,'trademode':0,
                'precision':0,'pricetick':1,'volscale':10,'name':'Synthetic steel',
                'exchg':'SHFE','session':'TRADING','holiday':'CHINA'}}},
            'contracts.json':{'SHFE':{'rb2701':{'name':'Synthetic steel','code':'rb2701','exchg':'SHFE',
                'product':'rb','maxlimitqty':500,'maxmarketqty':30,'minlimitqty':1,'minmarketqty':1,
                'opendate':20260116,'expiredate':20270115,'longmarginratio':.1,'shortmarginratio':.1}}},
            'sessions.json':{'TRADING':session},'holidays.json':{'CHINA':[]},'hots.json':{},
            'fees.json':{'SHFE.rb':FEES['fees']}}
        for name, value in documents.items():
            (base/name).write_text(json.dumps(value), encoding='utf-8')
        self.manager = PlusBacktestManager(self.workspace/'tasks', workspace=self.workspace)
        self.addCleanup(self.manager.shutdown)

    def run_task(self, source, kind='HFT', capital=100000, execution=None):
        task = self.manager.create('test-user','test-strategy',source,kind,'SHFE.rb.2701',
                                   'rb2701-synthetic-path',capital=capital,finance=FEES,execution=execution)
        deadline = time.monotonic()+35
        while time.monotonic() < deadline:
            detail = self.manager.detail('test-user', task['id'])
            if detail['task']['status'] in ('completed','failed','stopped') and 'exitcode' in detail['task']:
                self.assertEqual(detail['task']['status'], 'completed', detail['log_tail'])
                return detail
            time.sleep(.03)
        self.fail('Native replay did not exit')

    def test_fees_slippage_and_insufficient_margin(self):
        detail = self.run_task(HFT)
        self.assertEqual([x['volume'] for x in detail['trades']], [2,1,2,1])
        self.assertEqual([x['price'] for x in detail['trades']], [3303,3303,3297,3297])
        for key, value in [('commission',21),('realized',-180),('balance',99799),('margin',0),
                           ('available',99799),('slippage_cost',120)]:
            self.assertAlmostEqual(detail['summary'][key], value)
        rejected = self.run_task(HFT, capital=100)
        self.assertEqual(rejected['trades'], [])
        self.assertEqual(rejected['summary']['rejected_insufficient_funds'], 1)
        self.assertEqual(rejected['summary']['commission'], 0)
        self.assertEqual(rejected['summary']['available'], 100)

    def test_account_query_requires_ready_and_receives_native_response(self):
        source = "from wtpy import BaseHftStrategy\nfrom pathlib import Path\nimport json,time\nclass TestStrategy(BaseHftStrategy):\n def __init__(self,name,code,**kwargs):\n  super().__init__(name);self.code=code;self.stage=0;self.proof={}\n def on_init(self,ctx):\n  self.proof['before_ready']=ctx.__engine__.query_trade_account('plus')\n  self.proof['unknown_before']=ctx.__engine__.query_trade_account('missing')\n  assert self.proof['before_ready'] is False and self.proof['unknown_before'] is False\n  ctx.stra_sub_ticks(self.code)\n def on_tick(self,ctx,code,tick):\n  path=Path('generated/traders/plus/rtdata.json')\n  if self.stage==0:\n   self.stage=1;self.proof['before']=json.loads(path.read_text())['funds']['CNY']['updated_at']\n  elif self.stage==1:\n   self.stage=2\n   self.proof['accepted']=ctx.__engine__.query_trade_account('plus')\n   self.proof['unknown_ready']=ctx.__engine__.query_trade_account('missing')\n   assert self.proof['accepted'] is True and self.proof['unknown_ready'] is False\n  elif self.stage==2:\n   self.stage=3;self.proof['after']=json.loads(path.read_text())['funds']['CNY']['updated_at']\n   assert self.proof['after']>self.proof['before'],self.proof\n   Path('query-proof.json').write_text(json.dumps(self.proof))\n"
        detail = self.run_task(source)
        proof = json.loads((self.manager.root/detail['task']['id']/'query-proof.json').read_text())
        self.assertFalse(proof['before_ready'])
        self.assertFalse(proof['unknown_before'])
        self.assertTrue(proof['accepted'])
        self.assertFalse(proof['unknown_ready'])
        self.assertGreater(proof['after'], proof['before'])
        self.assertEqual(detail['orders'], [])
        self.assertEqual(detail['trades'], [])

    def test_twap_and_vwap_use_distinct_native_schedules(self):
        schedules = {}
        for kind in ('twap','vwap'):
            execution = {'id':kind,'params':{'begin_time':900,'end_time':905,'tail_secs':5,
                         'total_times':5,'price_mode':2,'price_offset':10}}
            if kind == 'vwap':
                execution['volume_weights'] = [1,1,6,1,1]
            detail = self.run_task(CTA, 'CTA', execution=execution)
            self.assertEqual(sum(x['volume'] for x in detail['trades']), 10)
            self.assertEqual(sum(x['volume'] for x in detail['positions']), 10)
            self.assertEqual(detail['summary']['commission'], 20)
            schedules[kind] = [(x['time'],x['volume']) for x in detail['trades']]
            self.assertGreater(len({x[0] for x in schedules[kind]}), 2)
        self.assertNotEqual(schedules['twap'], schedules['vwap'])


if __name__ == '__main__':
    unittest.main()
