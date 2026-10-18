import json,tempfile,time,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from wtpy.StrategyRuntime import StrategyRuntime,atomic_json
from wtpy.monitor.MarketSessions import session_status
class Tests(unittest.TestCase):
 def test_queries(self):
  with tempfile.TemporaryDirectory()as path:
   called=[];r=StrategyRuntime(path,query_fund=lambda channel:called.append(channel)or True)
   try:
    atomic_json(Path(path)/"query-fund.json",{"revision":"one","channel":"mock","expires_at":time.time()*1000+5000})
    end=time.monotonic()+3
    while time.monotonic()<end:
     state=json.loads((Path(path)/"status.json").read_text())
     if (state.get("query_fund") or {}).get("revision")=="one":break
     time.sleep(.02)
    self.assertTrue(state["query_fund"]["accepted"]);self.assertEqual(called,["mock"])
    self.assertIn("query_fund",state["capabilities"])
    time.sleep(.3);self.assertEqual(called,["mock"])
    atomic_json(Path(path)/"query-fund.json",{"revision":"expired","channel":"mock","expires_at":1})
    end=time.monotonic()+3
    while time.monotonic()<end:
     state=json.loads((Path(path)/"status.json").read_text())
     if (state.get("query_fund")or{}).get("revision")=="expired":break
     time.sleep(.02)
    self.assertFalse(state["query_fund"]["accepted"]);self.assertEqual(called,["mock"])
   finally:r.close()
   self.assertFalse(r._thread.is_alive())
 def test_sessions(self):
  c={"exchange":"SHFE","session":"night","holiday":"CHINA"}
  ss={"night":{"name":"Test","sections":[{"from":2100,"to":230},{"from":900,"to":1130},{"from":1330,"to":1500}]}}
  checks=[("2026-10-18T10:00",False),("2026-10-17T01:00",True),("2026-10-19T01:00",False),("2026-09-30T21:10",False),("2026-10-19T10:00",True),("2026-10-19T12:00",False)]
  for text,expected in checks:
   with self.subTest(time=text):
    value=session_status(c,ss,{},datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Asia/Shanghai")))
    self.assertEqual(value["session_open"],expected)
  self.assertIsNone(session_status(c,ss,{},datetime(2027,2,1,10,tzinfo=ZoneInfo("Asia/Shanghai")))["session_open"])
if __name__=="__main__":unittest.main()
