"""A closed night bar cannot jump the reader clock beyond the next day session."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest

from wtpy.WtCoreDefs import WTSTickStruct, WTSBarStruct
from wtpy.wrapper.WtDtHelper import WtDataHelper
from . import test_plus_execution as execution_tests

SOURCE='''from wtpy import BaseCtaStrategy
from pathlib import Path
import json
class ClockStrategy(BaseCtaStrategy):
 def __init__(self,name,code,period,**kwargs):
  super().__init__(name);self.code=code;self.period=period;self.bars=[];self.calcs=[];self.tick_clock=0
 def on_init(self,ctx):
  ctx.stra_sub_ticks(self.code)
  for p in ('m1','m5'):
   ctx.stra_get_bars(self.code,p,20,isMain=p==self.period);ctx.stra_sub_bar_events(self.code,p)
 def on_bar(self,ctx,code,period,bar):
  stamp=int(bar['time']);stamp=stamp+199000000000 if stamp<100000000000 else stamp
  clock=ctx.stra_get_date()*10000+ctx.stra_get_time()
  assert stamp<=clock,(period,stamp,clock)
  assert stamp>=self.tick_clock,('late close after a newer Tick',period,stamp,self.tick_clock)
  self.bars.append([period,stamp,clock])
 def on_tick(self,ctx,code,tick):
  self.tick_clock=int(tick['action_date'])*10000+int(tick['action_time'])//100000
 def on_calculate(self,ctx):
  self.calcs.append(ctx.stra_get_date()*10000+ctx.stra_get_time())
  for p in ('m1','m5'):ctx.stra_get_bars(self.code,p,20,isMain=p==self.period)
 def on_backtest_end(self,ctx):
  Path('clock-proof.json').write_text(json.dumps(dict(bars=self.bars,calcs=self.calcs)))
'''


@unittest.skipUnless(execution_tests.NATIVE and (Path(execution_tests.NATIVE)/'libWtPorter.so').is_file(),
                     'Set WT_PLUS_NATIVE_DIR to the native replay libraries')
class NativeReplayClockTests(unittest.TestCase):
    def setUp(self):
        execution_tests.NativeExecutionTests.setUp(self)
        base=self.workspace/'validation/replay/common'
        (base/'sessions.json').write_text(json.dumps({'TRADING':{'name':'Synthetic night and day','offset':300,
            'auction':{'from':2059,'to':2100},'sections':[{'from':2100,'to':230},{'from':900,'to':1130},{'from':1300,'to':1515}]}}))
        starts=[('2026-10-16 21:00',20261019,6),('2026-10-16 22:58',20261019,3),('2026-10-19 08:59',20261019,1),('2026-10-19 09:00',20261019,6),
                ('2026-10-19 21:00',20261020,6),('2026-10-19 23:58',20261020,5),
                ('2026-10-20 08:59',20261020,1),('2026-10-20 09:00',20261020,7)]
        ticks=[]
        for text,trading_day,minutes in starts:
            for minute in range(minutes):
                for second in [10,30]:
                    when=dt.datetime.strptime(text,'%Y-%m-%d %H:%M')+dt.timedelta(minutes=minute,seconds=second)
                    tick=WTSTickStruct();tick.exchg=b'SHFE';tick.code=b'rb2701'
                    tick.action_date=int(when.strftime('%Y%m%d'));tick.trading_date=trading_day
                    tick.action_time=int(when.strftime('%H%M%S'))*1000
                    tick.price=3300;tick.open=tick.high=tick.low=3300
                    tick.volume=1;tick.total_volume=len(ticks)+1;tick.open_interest=10000
                    tick.bid_price_0=3299;tick.ask_price_0=3301;tick.bid_qty_0=tick.ask_qty_0=2
                    tick.upper_limit=4000;tick.lower_limit=2500;tick.pre_close=tick.pre_settle=3300
                    ticks.append(tick)
        path=self.workspace/'multi-session.dsb'
        self.assertTrue(WtDataHelper().store_ticks(str(path),(WTSTickStruct*len(ticks))(*ticks),len(ticks)))
        (self.workspace/'run').mkdir(exist_ok=True)
        (self.workspace/'run/plus-datasets.json').write_text(json.dumps([dict(id='multi-session',label='Synthetic night/day clock',
            code='SHFE.rb.2701',start=202610162100,end=202610200906,ticks=len(ticks),kind='dsb',source='synthetic',file=str(path),basefiles=str(base))]))

    def test_night_day_midnight_weekend_keep_bar_clock_for_main_periods(self):
        for period in ['m1','m5']:
            with self.subTest(period=period):
                result=execution_tests.NativeExecutionTests.run_task(self,SOURCE,'CTA',dataset='multi-session',period=period)
                proof=json.loads((self.manager.root/result['task']['id']/'clock-proof.json').read_text())
                self.assertTrue(all(stamp<=clock for _,stamp,clock in proof['bars']))
                day_session=[stamp for stamp in proof['calcs'] if stamp//10000==20261020 and 900<=stamp%10000<1000]
                self.assertGreaterEqual(len(day_session),5 if period=='m1' else 1)
                self.assertTrue(any(stamp//10000==20261020 and stamp%10000<100 for stamp in proof['calcs']))
                self.assertEqual(result['orders'],[])


@unittest.skipUnless(execution_tests.NATIVE and (Path(execution_tests.NATIVE)/'libWtPorter.so').is_file(),
                     'Set WT_PLUS_NATIVE_DIR to the native replay libraries')
class ReplayWarmupTests(unittest.TestCase):
    def test_seed_keeps_closed_minute_bars_and_completed_days(self):
        from wtpy.monitor.PlusReplayRunner import seed_history
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = WtDataHelper()
            for period, directory in [('m1', 'min1'), ('m5', 'min5'), ('d', 'day')]:
                bars = []
                for date, clock in [(20261019, 1500), (20261020, 900), (20261020, 905)]:
                    bar = WTSBarStruct()
                    bar.date = date
                    bar.time = date*10000+clock-199000000000
                    bar.open = bar.high = bar.low = bar.close = 3300
                    bars.append(bar)
                source = root/'snapshot'/'his'/directory/'SHFE'/'rb2701.dsb'
                source.parent.mkdir(parents=True)
                self.assertTrue(helper.store_bars(str(source), (WTSBarStruct*len(bars))(*bars), len(bars), period))
            first = WTSTickStruct()
            first.exchg = b'SHFE'; first.code = b'rb2701'
            first.action_date = 20261020; first.action_time = 90010000
            counts = seed_history(root/'task', {'storage': str(root/'snapshot')}, first)
            self.assertEqual(counts, {'m1': 2, 'm5': 2, 'd': 1})
            for period, directory in [('m1', 'min1'), ('m5', 'min5'), ('d', 'day')]:
                data = helper.read_dsb_bars(str(root/'task'/'storage'/'his'/directory/'SHFE'/'rb2701.dsb'))
                self.assertEqual(len(data), counts[period])
                self.assertEqual(float(data.ndarray[-1]['close']), 3300)
            self.assertEqual(seed_history(root/'legacy', None, first), {})


if __name__=='__main__':unittest.main()
