"""Closed-bar visibility, volume conservation, and next-event execution in WT."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import test_plus_execution as fixtures
from wtpy.WtCoreDefs import WTSBarStruct
from wtpy.wrapper.WtDtHelper import WtDataHelper

CHILD = '''import json,sys,os
from pathlib import Path
from wtpy import WtBtEngine,EngineType,BaseCtaStrategy
root=Path(sys.argv[1]);mode=sys.argv[2];os.chdir(root)
class Strategy(BaseCtaStrategy):
 def __init__(self):super().__init__('bar-check');self.ticks=[];self.bars=[]
 def on_init(self,c):
  c.stra_get_bars('SHFE.rb.2701','m1',10,isMain=True)
  c.stra_sub_ticks('SHFE.rb.2701');c.stra_sub_bar_events('SHFE.rb.2701','m1')
 def on_tick(self,c,code,t):
  self.ticks.append(dict(time=c.stra_get_time(),price=t['price'],volume=t['volume'],total=t['total_volume'],high=t['high'],low=t['low'],position=c.stra_get_position(code)))
 def on_bar(self,c,code,period,bar):
  self.bars.append(dict(time=c.stra_get_time(),high=bar['high'],low=bar['low'],close=bar['close']))
  if len(self.bars)==1:c.stra_set_position(code,1)
base=root/'validation/replay/common'
basefiles={k:str(base/f) for k,f in [('commodity','commodities.json'),('contract','contracts.json'),('session','sessions.json'),('holiday','holidays.json'),('hot','hots.json')]}
engine=WtBtEngine(EngineType.ET_CTA,json.dumps({'root':{'level':'off','sinks':[]}}),False,outDir=str(root/('output-'+mode)))
engine.init_with_config('',{'env':{'mocker':'cta','fees':str(base/'fees.json')},'replayer':{'basefiles':basefiles,'mode':'bin','store':{'path':str(root/'market')},'tick':False,'bar_simulation':mode}})
engine.configBacktest(202610120900,202610120904);engine.commitBTConfig()
strategy=Strategy();engine.set_cta_strategy(strategy,fill_model='legacy_cta')
engine.run_backtest(False);engine.raise_callback_error();engine.release_backtest()
print('RESULT '+json.dumps({'ticks':strategy.ticks,'bars':strategy.bars}))
'''


@unittest.skipUnless(fixtures.NATIVE and (Path(fixtures.NATIVE)/'libWtPorter.so').is_file(), 'Requires built WT native runtime')
class BarSimulationTests(unittest.TestCase):
    def test_closed_bar_visibility_and_legacy_volume(self):
        fixture=fixtures.NativeExecutionTests();fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root=fixture.workspace
        path=root/'market/his/min1/SHFE/rb2701.dsb';path.parent.mkdir(parents=True)
        bars=[]
        # First bar is native warm-up. Two later bars are replayed.
        for minute,values in [(901,(100,120,80,105,40)),(902,(106,130,70,110,60)),(903,(111,140,60,115,80))]:
            bar=WTSBarStruct();bar.date=20261012;bar.time=202610120000+minute-199000000000
            bar.open,bar.high,bar.low,bar.close,bar.vol=values;bars.append(bar)
        self.assertTrue(WtDataHelper().store_bars(str(path),(WTSBarStruct*len(bars))(*bars),len(bars),'m1'))
        child=root/'check.py';child.write_text(CHILD)
        results={}
        for model in ('close_only','legacy_ohlc'):
            process=subprocess.run([sys.executable,str(child),str(root),model],capture_output=True,text=True,timeout=30)
            self.assertEqual(process.returncode,0,process.stdout[-2000:]+process.stderr[-2000:])
            result=json.loads(next(line[7:] for line in process.stdout.splitlines() if line.startswith('RESULT ')))
            self.assertEqual(sum(tick['volume'] for tick in result['ticks']),140)
            self.assertEqual(result['ticks'][-1]['total'],140)
            self.assertEqual([row['close'] for row in result['bars']],[110,115])
            results[model]=result
        closed=results['close_only']['ticks']
        self.assertEqual([row['price'] for row in closed],[110,115])
        self.assertEqual([row['position'] for row in closed],[0,1])
        self.assertEqual(closed[0]['high'],110)
        self.assertEqual(closed[0]['low'],110)
        legacy=results['legacy_ohlc']['ticks']
        self.assertEqual(len(legacy),8)
        self.assertEqual([row['volume'] for row in legacy],[0,0,0,60,0,0,0,80])


if __name__=='__main__':unittest.main()
