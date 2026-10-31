"""Drive the real collector across a weekend, without a broker or network."""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext

CLOCK = r'''
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
int clock_gettime(clockid_t clock, struct timespec *value) {
 int result=syscall(SYS_clock_gettime,clock,value);
 const char *offset=getenv("WT_TEST_CLOCK_OFFSET");
 if(!result && clock==CLOCK_REALTIME && offset) value->tv_sec+=strtoll(offset,0,10);
 return result;
}
'''


def child():
    import ctypes
    import calendar
    from wtpy.wrapper.PlatformHelper import PlatformHelper
    from wtpy import WtDtEngine
    from wtpy.ExtModuleDefs import BaseExtParser
    from wtpy.WtCoreDefs import WTSTickStruct
    from wtpy.wrapper.WtDtHelper import WtDataHelper
    os.environ['TZ']='Asia/Shanghai';time.tzset()
    def clock(text):
        desired=calendar.timegm(dt.datetime.strptime(text,'%Y-%m-%d %H:%M:%S').timetuple())-8*3600
        # CLOCK_MONOTONIC is not changed by the test shim.
        real=time.clock_gettime(time.CLOCK_MONOTONIC)+REALTIME_ORIGIN
        os.environ['WT_TEST_CLOCK_OFFSET']=str(int(desired-real))
    # The parent supplies the real wall/monotonic origin before preloading the shim.
    REALTIME_ORIGIN=float(os.environ['WT_TEST_REALTIME_ORIGIN'])
    workspace=os.environ.get('WT_TEST_SHARED_STORAGE')
    with nullcontext(workspace) if workspace else tempfile.TemporaryDirectory(prefix='wt-collector-weekend-') as folder:
        root=Path(folder);os.chdir(root)
        library=root/'libWtDtPorter.so';shutil.copyfile(os.environ['WT_COLLECTOR_LIBRARY'],library)
        if not (root/'libWtDataStorage.so').exists():
            (root/'libWtDataStorage.so').symlink_to(os.environ.get('WT_COLLECTOR_STORAGE','/opt/wt/native/libWtDataStorage.so'))
        original=PlatformHelper.getModule
        PlatformHelper.getModule=staticmethod(lambda name,subdir='':str(library) if name=='WtDtPorter' else original(name,subdir))
        holiday=bool(int(os.environ.get('WT_TEST_MONDAY_HOLIDAY','0')))
        documents={
            'commodities.json':{'SHFE':{'rb':{'covermode':1,'pricemode':1,'category':1,'trademode':0,
                'precision':0,'pricetick':1,'volscale':10,'name':'Synthetic','exchg':'SHFE','session':'FN2300','holiday':'CHINA'}}},
            'contracts.json':{'SHFE':{'rb2701':{'name':'Synthetic','code':'rb2701','exchg':'SHFE','product':'rb',
                'opendate':20260116,'expiredate':20270115,'longmarginratio':.1,'shortmarginratio':.1}}},
            'sessions.json':{'FN2300':{'name':'Synthetic night and day','offset':300,'auction':{'from':2059,'to':2100},
                'sections':[{'from':2100,'to':2300},{'from':900,'to':1015},{'from':1030,'to':1130},{'from':1330,'to':1500}]}},
            'holidays.json':{'CHINA':[20261026] if holiday else []},
            'state.json':{'FN2300':{'inittime':2050,'closetime':1515,'proctime':1600}}}
        for name,value in documents.items():(root/name).write_text(json.dumps(value))
        config=dict(basefiles=dict(commodity=str(root/'commodities.json'),contract=str(root/'contracts.json'),
            session=str(root/'sessions.json'),holiday=str(root/'holidays.json')),
            statemonitor=str(root/'state.json'),parsers=[],broadcaster={'active':False},
            writer=dict(module='WtDataStorage',path=str(root/'storage'),groupsize=20,async_=False,savelog=True))
        config['writer']['async']=config['writer'].pop('async_')
        archive=bool(int(os.environ.get('WT_TEST_ARCHIVE','0')))
        helper=WtDataHelper()
        originals={}
        if archive:
            from wtpy.WtCoreDefs import WTSBarStruct
            for period,directory,times in [('m1','min1',[202610231500,202610260901,202610260902,202610260903]),
                                           ('m5','min5',[202610231500,202610260905,202610260910])]:
                bars=[]
                for stamp in times:
                    row=WTSBarStruct(date=stamp//10000,time=stamp-199000000000,
                        open=3310,high=3311,low=3309,close=3310,vol=7)
                    bars.append(row)
                path=root/f'storage/his/{directory}/SHFE/rb2701.dsb';path.parent.mkdir(parents=True,exist_ok=True)
                assert helper.store_bars(str(path),(WTSBarStruct*len(bars))(*bars),len(bars),period)
                originals[period]={int(row.time):row for row in bars}
        phase=int(os.environ.get('WT_TEST_BASELINE_PHASE','0'))
        clock({1:'2026-10-26 09:00:00',2:'2026-10-26 10:30:00',3:'2026-10-27 09:00:00'}.get(phase,'2026-10-25 14:06:00'))
        engine=WtDtEngine();engine.init_with_config(config,dict(root=dict(level='info',sinks=[dict(type='console_sink')])))
        engine.add_exetended_parser(BaseExtParser('fixture'));engine.run(True)
        def tick(date,stamp,total=1):
            value=WTSTickStruct();value.exchg=b'SHFE';value.code=b'rb2701'
            value.action_date=date;value.trading_date=date if phase else 20261027 if holiday else 20261026
            value.action_time=stamp;value.price=value.open=value.high=value.low=3300
            value.bid_price_0=3299;value.ask_price_0=3301;value.bid_qty_0=value.ask_qty_0=2
            value.volume=1;value.total_volume=total;value.open_interest=100
            engine.push_quote_from_extended_parser('fixture',ctypes.pointer(value),1)
            time.sleep(.2)
        def count():
            path=root/'storage/rt/ticks/SHFE/rb2701.dmb'
            if not path.exists():return 0
            rows=helper.read_dmb_ticks(str(path))
            return len(rows) if rows is not None else 0
        time.sleep(1.3)
        if phase:
            day=20261027 if phase==3 else 20261026
            times=(103001000,103001500) if phase==2 else (90001000,90001500)
            totals={1:(100,101),2:(1171353,1171358),3:(7,9)}[phase]
            if phase==2:
                before=count();tick(day,103000500,99)
                assert count()==before,'An invalid first snapshot bypassed the cumulative-volume check'
            for stamp,total in zip(times,totals):tick(day,stamp,total)
            data=helper.read_dmb_ticks(str(root/'storage/rt/ticks/SHFE/rb2701.dmb')).ndarray
            expected={1:(100,1),2:(0,5),3:(7,2)}[phase]
            assert tuple(float(row['volume']) for row in data[-2:])==expected,(phase,data[-2:].tolist())
            engine.release()
            import csv
            rows=list(csv.reader((root/f'storage/rt/ticks/SHFE/rb2701.{day}.csv').open()))
            assert float(rows[-1][6])==totals[-1],('CSV cumulative volume lost precision',rows[-1])
            return
        tick(20261025,140600000);assert count()==0,'Sunday accepted a Tick'
        clock('2026-10-24 09:01:00');time.sleep(1.3)
        tick(20261024,90100000);assert count()==0,'Saturday daytime recovered from holiday'
        clock('2026-10-26 09:01:00');time.sleep(2.4)
        tick(20261026,90100000)
        if holiday:
            assert count()==0,'Configured Monday holiday accepted a Tick'
            clock('2026-10-27 09:01:00');time.sleep(2.4);tick(20261027,90100000)
        assert count()==1,'The next trading morning remained in holiday state'
        day=27 if holiday else 26
        clock(f'2026-10-{day} 10:20:00');time.sleep(1.3);tick(20261000+day,102000000)
        assert count()==1,'The intraday break accepted a Tick'
        clock(f'2026-10-{day} 10:31:00');time.sleep(1.3);tick(20261000+day,103100000)
        assert count()==2,'The collector did not resume after the intraday break'
        clock(f'2026-10-{day} 11:45:00');time.sleep(1.3);tick(20261000+day,114500000)
        assert count()==2,'The lunch break accepted a Tick'
        clock(f'2026-10-{day} 13:31:00');time.sleep(1.3);tick(20261000+day,133100000)
        assert count()==3,'The collector did not resume after lunch'
        if archive:
            clock('2026-10-26 15:20:00');time.sleep(1.3)
            clock('2026-10-26 16:00:00');time.sleep(2.4)
            for period,directory in [('m1','min1'),('m5','min5')]:
                data=helper.read_dsb_bars(str(root/f'storage/his/{directory}/SHFE/rb2701.dsb')).ndarray
                stamps=[int(row['time']) for row in data]
                assert stamps==sorted(set(stamps)),(period,'Archive duplicated or reordered bars',stamps)
                actual={int(row['time']):row for row in data}
                assert len(actual)>len(originals[period]),'The archive did not add realtime bars'
                for stamp,row in originals[period].items():
                    assert actual[stamp]['close']==row.close and actual[stamp]['volume']==row.vol,'Existing history changed'
        print(json.dumps(dict(recovered=True,holiday=holiday,ticks=count())),flush=True)
        engine.release()


@unittest.skipUnless(sys.platform=='linux' and os.environ.get('WT_COLLECTOR_LIBRARY'),
                     'Set WT_COLLECTOR_LIBRARY to the collector library on Linux')
class CollectorWeekendTests(unittest.TestCase):
    def test_same_day_restart_rebases_without_discarding_new_day_auction_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'clock.c';source.write_text(CLOCK)
            library=Path(directory)/'clock.so'
            subprocess.run(['cc','-shared','-fPIC',str(source),'-o',str(library)],check=True)
            shared=Path(directory)/'data';shared.mkdir()
            for phase in [1,2,3]:
                env={**os.environ,'LD_PRELOAD':str(library),'PYTHONFAULTHANDLER':'1',
                     'WT_TEST_BASELINE_PHASE':str(phase),'WT_TEST_SHARED_STORAGE':str(shared),
                     'WT_TEST_REALTIME_ORIGIN':str(time.time()-time.monotonic())}
                value=subprocess.run([sys.executable,__file__,'--child'],env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(value.returncode,0,(value.stdout+value.stderr)[-4000:])

    def test_intraday_history_survives_closing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'clock.c';source.write_text(CLOCK)
            library=Path(directory)/'clock.so'
            subprocess.run(['cc','-shared','-fPIC',str(source),'-o',str(library)],check=True)
            env={**os.environ,'LD_PRELOAD':str(library),'PYTHONFAULTHANDLER':'1','WT_TEST_ARCHIVE':'1',
                 'WT_TEST_REALTIME_ORIGIN':str(time.time()-time.monotonic())}
            value=subprocess.run([sys.executable,__file__,'--child'],env=env,capture_output=True,text=True,timeout=35)
            self.assertEqual(value.returncode,0,(value.stdout+value.stderr)[-5000:])

    def test_weekend_and_calendar_holiday_recover_without_accepting_closed_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'clock.c';source.write_text(CLOCK)
            library=Path(directory)/'clock.so'
            subprocess.run(['cc','-shared','-fPIC',str(source),'-o',str(library)],check=True)
            for holiday in [False,True]:
                with self.subTest(monday_holiday=holiday):
                    env={**os.environ,'LD_PRELOAD':str(library),'PYTHONFAULTHANDLER':'1','WT_TEST_MONDAY_HOLIDAY':str(int(holiday)),
                         'WT_TEST_REALTIME_ORIGIN':str(time.time()-time.monotonic())}
                    value=subprocess.run([sys.executable,__file__,'--child'],env=env,capture_output=True,text=True,timeout=35)
                    self.assertEqual(value.returncode,0,(value.stdout+value.stderr)[-5000:])


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:unittest.main()
