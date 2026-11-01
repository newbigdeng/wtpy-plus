"""Focused coverage for asset boundaries and recorded trades."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wtpy.monitor.AssetMarkets import asset_root, dataset_code, instrument
from wtpy.monitor.AssetWorkspace import register_asset_routes
from wtpy.monitor.CryptoArchive import connect_archive, replay_bars


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict("os.environ", {"WT_ASSET_ROOT":str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.user = {"loginid":"tester","role":"admin"}
        self.app = FastAPI()
        register_asset_routes(self.app, self, lambda *_:(True,self.user), self.root)
        self.client = TestClient(self.app)

    def log_action(self, *_): pass

    def test_libraries_do_not_share_ids_or_versions(self):
        body = dict(library_id="Example", name="Example", source="from wtpy import BaseCtaStrategy\nclass Example(BaseCtaStrategy):\n    pass\n")
        self.assertEqual(self.client.post("/mgr/assets/option/strategy_library_save",json=body).json()["result"],0)
        for asset, count in (("stock",0),("option",1),("crypto",0)):
            self.assertEqual(len(self.client.post(f"/mgr/assets/{asset}/strategy_library",json={}).json()["strategies"]),count)
        self.user["role"]="guest"
        self.assertEqual(self.client.post("/mgr/assets/option/strategy_library_get",json={"library_id":"Example"}).json()["result"],-403)

    def test_single_symbol_and_option_chain(self):
        self.assertEqual(instrument("futures","CZCE.SR.2701"),("CZCE","SR701"))
        with self.assertRaises(ValueError): instrument("option","DCE.m2701")
        with self.assertRaises(ValueError): instrument("stock","SSE.600000,sz000001")
        with self.assertRaises(ValueError): asset_root(self.root,"../stock")
        row=dict(product="m_o",expiredate=20261207,option={"strikeprice":3000,"underlying":"m2701"})
        self.assertEqual(dataset_code("option","DCE","m2701-C-3000",row),"DCE.m2701.C.3000")
        common=self.root/"option/common";common.mkdir(parents=True)
        (common/"contracts.json").write_text(json.dumps({"DCE":{"m2701-C-3000":row}}))
        response=self.client.post("/mgr/assets/option/chain",json={"underlying":"DCE.m2701"}).json()
        self.assertEqual(response["contracts"][0]["symbol"],"DCE.m2701-C-3000")
        with patch("wtpy.monitor.AssetWorkspace.http_quote",return_value={"last":10}) as feed:
            self.assertEqual(self.client.post("/mgr/assets/stock/quote",json={"symbol":"SSE.600000"}).json()["quote"]["last"],10)
            feed.assert_called_once_with("stock","SSE.600000")

    def test_trade_archive_drops_partial_edges_and_rejects_missing_trades(self):
        path=self.root/"matches.sqlite"
        stamp=int(datetime(2026,1,1,tzinfo=timezone.utc).timestamp()*1000)
        db=connect_archive(path)
        with db:
            db.executemany("INSERT INTO trades VALUES('BTC-USD',?,?,?,?)",
                [(i,stamp+sec*1000,px,1) for i,sec,px in ((1,0,10),(2,60,11),(3,61,12),(4,120,13))])
        bars=list(replay_bars(path,"BTC-USD","m1",202601010000,202601010003))
        self.assertEqual([(b["open"],b["close"],b["vol"]) for b in bars],[(11,12,2)])
        with db: db.execute("DELETE FROM trades WHERE id=3")
        db.close()
        with self.assertRaisesRegex(ValueError,"缺失"):list(replay_bars(path,"BTC-USD","m1",202601010000,202601010003))
