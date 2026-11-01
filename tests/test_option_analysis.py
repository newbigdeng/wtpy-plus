import json
import math
from pathlib import Path
import tempfile
import time
import unittest

import QuantLib as ql
from wtpy.monitor.OptionModels import contract_metrics, kirk_price, qdate, variance_surface
from wtpy.monitor.OptionDesk import OptionDesk
from wtpy.monitor.OptionDeskFeed import DeskRequest, desk_plan
from wtpy.monitor.OptionFrames import publish_frame, read_option_frame


class OptionAnalyticsTests(unittest.TestCase):
    def test_black76_roundtrip_and_units(self):
        day = qdate(20261030)
        t, rate, f, vol = 46/365,.02,3000,.3
        discount = math.exp(-rate*t)
        density = math.exp(-.5*(vol*math.sqrt(t)/2)**2)/math.sqrt(2*math.pi)
        for call in (True,False):
            side = ql.Option.Call if call else ql.Option.Put
            price = ql.blackFormula(side,f,f,vol*math.sqrt(t),discount)
            m = contract_metrics(f,f,price,46,rate,call,"black76",day)
            self.assertAlmostEqual(m["iv"],vol,7)
            self.assertAlmostEqual(m["gamma"],discount*density/(f*vol*math.sqrt(t)),7)
            self.assertAlmostEqual(m["vega"],discount*f*math.sqrt(t)*density/100,6)
            self.assertAlmostEqual(m["rho"],-t*price/100,6)
            self.assertAlmostEqual(m["theta"],ql.blackFormula(side,f,f,vol*math.sqrt(45/365),math.exp(-rate*45/365))-price,6)

    def test_american_implied_vol_reprices_and_rejects_intrinsic(self):
        day = qdate(20261030)
        a = contract_metrics(99,100,5,60,.05,True,"american",day)
        b = contract_metrics(99,100,5,60,.05,True,"black76",day)
        self.assertLess(a["iv"],b["iv"])
        self.assertAlmostEqual(a["theoretical"],5,6)
        with self.assertRaises(ValueError):
            contract_metrics(105,100,5,60,.05,True,"american",day)

    def test_surface_interpolates_variance_without_extrapolation(self):
        today=qdate(20261030)
        rows=[dict(strike=k,expiry=e,iv=v) for k in (95,105) for e,v in ((20261119,.2),(20261209,.4))]
        surface=variance_surface(rows,today,(100,today+30))
        self.assertAlmostEqual(surface["query_iv"],math.sqrt((20*.2**2+40*.4**2)/60))
        self.assertNotIn("query_iv",variance_surface(rows,today,(90,today+30)))
        self.assertIn("message",variance_surface(rows[:2],today))

    def test_kirk_matches_discounted_black_spread(self):
        f1,f2,strike,v1,v2,corr,t=105,100,3,.3,.2,.4,45/365
        b=f2/(f2+strike)
        vol=math.sqrt(v1**2+b*b*v2*v2-2*corr*b*v1*v2)
        expected=ql.blackFormula(ql.Option.Call,f2+strike,f1,vol*math.sqrt(t),math.exp(-.025*t))
        self.assertAlmostEqual(kirk_price(f1,f2,v1,v2,strike,45,.025,corr,1,qdate(20261030)),expected,9)

    def test_scope_stale_books_and_frame_lifetime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"reference").mkdir()
            row=dict(symbol="DCE.m2701-C-3000",side="看涨",expiry=20261216,strike=3000)
            (root/"reference/chain.json").write_text(json.dumps(dict(source="test",imported="2026-11-01",chains={"DCE.m2701":[row]})))
            spec=DeskRequest(chains=[dict(underlying="DCE.m2701",symbols=[row["symbol"]])],mode="simulation")
            plan=desk_plan(root,spec);self.assertEqual(set(plan["symbols"]),{("DCE","m2701"),("DCE","m2701-C-3000")})
            desk=OptionDesk(plan);now=time.time();session="a"*24
            book=dict(received=now,ask_size=1,bid_size=1,time="20261030 11:00:00.000",trading_day="20261030")
            quotes={"DCE.m2701":dict(book,bid=2999,ask=3001),row["symbol"]:dict(book,bid=109,ask=111)}
            frame=desk.snapshot(quotes,session,now)
            self.assertGreater(frame["rows"][0]["iv"],0)
            folder=root/"analysis"/session;folder.mkdir(parents=True)
            publish_frame(folder,frame)
            self.assertEqual(read_option_frame(root,session,mode="simulation")["trading_day"],"20261030")
            with self.assertRaises(ValueError):read_option_frame(root,session,mode="session")
            self.assertIn("reason",desk.snapshot(quotes,session,now+61)["rows"][0])
            quotes[row["symbol"]]["bid"]=115
            self.assertNotIn("iv",desk.snapshot(quotes,session,now)["rows"][0])
