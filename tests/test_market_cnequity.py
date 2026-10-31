import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_market_subscriptions as fixtures
from wtpy.monitor.MarketHistory import MarketHistory, days_between, register_market_history_routes


class CNEquityIntegrationTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.SubscriptionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.dog = fixture.root, fixture.processes
        self.store = fixtures.GroupStore(self.root)
        self.store.has_group = lambda groupid: groupid == "g"
        self.store.log_action = lambda *args: None
        config = self.root/"datahub/dtcfg.ctp.yaml"
        value = yaml.safe_load(config.read_text())
        value["writer"] = {"path": "../FUT_Data"}
        config.write_text(yaml.safe_dump(value))
        (self.root/"common/sessions.json").write_text(json.dumps({"NIGHT": {"sections": [{"from": 900, "to": 905}]}}))
        self.manifest = self.root/"profiles.json"
        self.state = self.root/"monitor"
        self.state.mkdir()
        (self.state/"market-providers.local.json").write_text(json.dumps({"cnequity": {
            "python": sys.executable, "config": str(self.root/"research.toml"), "data_root": str(self.root/"lake")}}))
        self.history = MarketHistory(self.store, self.dog, self.manifest, self.state)
        self.addCleanup(self.history.close)
        self.ctx = self.history.context("g", "SHFE.rb.2701", "m1", 20260803, 20260803)
        self.ctx["storage"].mkdir(parents=True)
        self.bars = [dict(date=20260803, time=202608030901-199000000000, open=100, high=102, low=99, close=101, vol=1)]

    def job(self):
        job = dict(id="synthetic",groupid="g",provider="cnequity",code="SHFE.rb.2701",period="m1",start_date=20260803,end_date=20260803,status="queued",error="")
        self.history.jobs.append(job)
        return job

    def test_live_strategy_prepares_lake_without_stopping_or_writing_wt(self):
        self.dog.running["g"] = True
        job = self.job()
        with patch.object(self.history, "fetch", return_value=self.bars), patch.object(self.history, "worker", return_value={"records": []}) as writer:
            self.history._run(job, self.ctx, True)
        self.assertEqual(job["status"], "ready")
        self.assertEqual(writer.call_count, 1)
        self.assertNotEqual(writer.call_args.args[-1], "merge")
        self.assertEqual(self.dog.actions, [])

    def test_collector_without_restart_consent_also_waits_for_import(self):
        job = self.job()
        with patch.object(self.history, "fetch", return_value=self.bars), patch.object(self.history, "worker", return_value={"records": []}) as writer:
            self.history._run(job, self.ctx, False)
        self.assertEqual(job["status"], "ready")
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.dog.actions, [])

    def test_stopped_processes_can_import_and_report_unavailable_history(self):
        self.dog.running["hub"] = False
        job = self.job()
        job["lake"] = {"missing_dates": [20260803]}
        with patch.object(self.history, "fetch", return_value=self.bars), patch.object(self.history, "worker", side_effect=[{"records": []},dict(rows=1,dates=[20260803])]) as writer:
            self.history._run(job, self.ctx, False)
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertIn("仍有 1 个交易日", job["message"])
        self.assertEqual(writer.call_count, 2)

    def test_existing_historical_timestamps_skip_writes_while_trading(self):
        self.dog.running["g"] = True
        job = self.job()
        records = [{"date":20260803,"stamp":202608030901,"source":"WT历史"}]
        with patch.object(self.history,"fetch",return_value=self.bars), patch.object(self.history,"worker",return_value={"records":records}) as writer:
            self.history._run(job,self.ctx,False)
        self.assertEqual((job["status"],job["rows"],job["already_present_rows"]),("completed",0,1))
        self.assertEqual(writer.call_count,1)
        self.assertEqual(self.dog.actions,[])

    def test_wt_and_lake_coverage_are_independent(self):
        lake = dict(rows=1,dates=[dict(date=20260803,rows=1,status="partial")])
        with patch.object(self.history, "worker", return_value={"records": []}), patch.object(self.history, "lake", return_value={"lake": lake}):
            result = self.history.coverage("g","SHFE.rb.2701","m1",20260803,20260803,"cnequity")
        row = result["dates"][0]
        self.assertEqual((row["rows"],row["status"],row["lake_rows"],row["lake_status"]),(0,"missing",1,"partial"))

    def test_extended_cnequity_window_does_not_expand_other_provider_limit(self):
        with self.assertRaisesRegex(ValueError,"31"):
            days_between(20260801,20261022)
        self.assertEqual(len(days_between(20260801,20261022,366)),83)

    def test_viewer_cannot_prepare_lake_or_submit_download(self):
        app = FastAPI()
        auth = lambda request,token: (True,{"loginid":"reader","role":"viewer"})
        history = register_market_history_routes(app,self.store,self.dog,auth,self.manifest,self.state)
        self.addCleanup(history.close)
        with patch.object(history,"providers",return_value=dict(can_backfill=True,can_prepare_lake=True,providers=[])):
            client = TestClient(app)
            response = client.post('/mgr/market/providers',json={"groupid":"g"}).json()
            self.assertFalse(response["can_prepare_lake"])
            self.assertFalse(response["can_backfill"])
            rejected = client.post('/mgr/market/backfill',json={"groupid":"g","provider":"cnequity","code":"SHFE.rb.2701","period":"d","start_date":20260803,"end_date":20260803}).json()
            self.assertEqual(rejected["result"],-403)


if __name__ == "__main__":
    unittest.main()
