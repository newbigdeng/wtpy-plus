"""Native DSB date selection and destructive-operation controls; no broker APIs."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from wtpy.WtCoreDefs import WTSBarStruct, WTSTickStruct
from wtpy.wrapper.WtDtHelper import WtDataHelper
from wtpy.monitor.HistoryCatalog import dataset_entries, prepare_native_dataset, register_history_catalog_routes, _inspect_many
from wtpy.monitor.HistoryDataWorker import dataset_records, merge


class HistoryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wt-history-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root/"profile"
        (self.profile/"datahub").mkdir(parents=True)
        common = self.profile/"common"
        common.mkdir()
        (common/"contracts.json").write_text(json.dumps({"SHFE":{"rb2701":{"product":"rb"}}}))
        for name in ("commodities", "sessions", "holidays", "hots", "fees"):
            (common/(name+".json")).write_text("{}")
        (self.profile/"datahub/dtcfg.ctp.yaml").write_text(json.dumps({"writer":{"path":"../FUT_Data"},
            "basefiles":{"contract":"../common/contracts.json", "commodity":"../common/commodities.json", "session":"../common/sessions.json", "holiday":"../common/holidays.json"}}))
        self.manifest = self.root/"profiles.json"
        self.manifest.write_text(json.dumps({"groups":{"g":[{"id":"test", "label":"测试", "root":str(self.profile), "datahub_id":"hub"}]}}))
        self.environment = patch.dict(os.environ, {"WT_CHANNEL_PROFILES":str(self.manifest), "WT_PLUS_WORKSPACE":str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        path=self.profile/"FUT_Data/his/min1/SHFE/rb2701.dsb"
        path.parent.mkdir(parents=True)
        bars=[]
        for day in (20261016,20261019):
            for minute in (901,902,903):
                bar=WTSBarStruct();bar.date=day;bar.time=day*10000+minute-199000000000
                bar.open=3300;bar.high=3304;bar.low=3299;bar.close=3302;bar.vol=12;bars.append(bar)
        self.assertTrue(WtDataHelper().store_bars(str(path),(WTSBarStruct*len(bars))(*bars),len(bars),"m1"))
        self.identifier="local_g_test_SHFE_rb2701_m1"

    def test_real_coverage_and_date_snapshot(self):
        row=dataset_entries(self.root)[0]
        self.assertEqual((row["rows"],row["start"],row["end"]),(6,202610160901,202610190903))
        folder=self.root/"task";folder.mkdir()
        data=prepare_native_dataset(self.root,self.identifier,202610190902,202610190902,folder)
        self.assertEqual(data["model"],"closed_bar_only")
        self.assertFalse(data["tick_enabled"])
        # Earlier rows remain available for warm-up, later rows are excluded.
        actual=WtDataHelper().read_dsb_bars(data["files"][0]).ndarray
        self.assertEqual(len(actual),5)
        self.assertTrue(Path(data["basefiles"]["contract"]).is_relative_to(folder))

    def test_empty_day_inside_total_range_is_rejected(self):
        folder=self.root/"task";folder.mkdir()
        with self.assertRaisesRegex(ValueError,"没有行情"):
            prepare_native_dataset(self.root,self.identifier,20261017,20261018,folder)

    def test_imported_history_updates_cached_catalog_immediately(self):
        self.assertEqual(dataset_entries(self.root)[0]["start"],202610160901)
        bar=dict(date=20260803,time=202608030901-199000000000,
                 open=3290,high=3300,low=3280,close=3295,vol=10)
        result=merge(dict(storage=str(self.profile/"FUT_Data"),exchange="SHFE",ticker="rb2701",
            period="m1",start_date=20260801,end_date=20260831,bars=[bar]),WtDataHelper())
        self.assertEqual(result["rows"],1)
        # No sleep or manual cache expiry: an imported DSB must be visible now.
        row=dataset_entries(self.root)[0]
        self.assertEqual((row["rows"],row["start"]),(7,202608030901))

    def test_imported_daily_data_does_not_create_minute_or_tick_history(self):
        bars=[dict(date=day,open=3290,high=3300,low=3280,close=3295,vol=10)
              for day in (20260803,20260901)]
        merge(dict(storage=str(self.profile/"FUT_Data"),exchange="SHFE",ticker="rb2701",
            period="d",start_date=20260801,end_date=20260930,bars=bars),WtDataHelper())
        rows={row["period"]:row for row in dataset_entries(self.root)}
        self.assertEqual(set(rows),{"m1","d"})
        self.assertEqual((rows["d"]["rows"],rows["d"]["start"]),(2,202608031500))
        self.assertEqual(rows["m1"]["start"],202610160901)

    def test_explicit_refresh_bypasses_realtime_inventory_ttl(self):
        realtime=self.root/"realtime.dmb";realtime.write_bytes(b"native-worker-placeholder")
        item=dict(id="test_realtime_refresh",period="m1",files=[str(realtime)])
        first=dict(rows=1,start=202610210901,end=202610210901,dates=[20261021])
        second=dict(rows=2,start=202610210901,end=202610210902,dates=[20261021])
        with patch("wtpy.monitor.HistoryCatalog.native_worker",side_effect=[{"entries":{item["id"]:first}},{"entries":{item["id"]:second}}]) as worker:
            self.assertEqual(_inspect_many([dict(item)])[0]["rows"],1)
            self.assertEqual(_inspect_many([dict(item)])[0]["rows"],1)
            self.assertEqual(_inspect_many([dict(item)],refresh=True)[0]["rows"],2)
            self.assertEqual(worker.call_count,2)

    def test_unknown_dataset_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError,"不存在"):
            prepare_native_dataset(self.root,"../../private",20261016,20261019,self.root)

    def test_final_realtime_bar_is_not_exposed_as_closed_history(self):
        path=self.profile/"FUT_Data/his/min1/SHFE/rb2701.dsb"
        data=WtDataHelper().read_dsb_bars(str(path))
        class Reader:
            def read_dmb_bars(inner,filename):return data
        rows, event_stamp=dataset_records(dict(files=[str(path.with_suffix(".dmb"))],period="m1"),Reader())
        self.assertEqual(len(rows),5)
        self.assertEqual(event_stamp(rows[-1]),202610190902)

    def test_catalog_ignores_symlinked_storage_subdirectory(self):
        directory=self.profile/"FUT_Data/his/min1/SHFE"
        outside=self.root/"outside";outside.mkdir()
        (directory/"rb2701.dsb").rename(outside/"rb2701.dsb")
        directory.rmdir();directory.symlink_to(outside,target_is_directory=True)
        self.assertEqual(dataset_entries(self.root),[])

    def make_client(self, role="superman", running=False, active_native=False, products=None):
        class Data:
            def get_groups(inner):return [{"id":"g","path":str(self.profile/"run")}]
            def log_action(inner,*args):pass
        class Dog:
            def isRunning(inner,app):return running if isinstance(running,bool) else app in running
        class Native:
            task_map={"task":object()} if active_native else {}
        app=FastAPI()
        register_history_catalog_routes(app,Data(),Dog(),lambda request,token:(True,{"loginid":"owner","role":role,"products":products or []}),
            manifest_path=self.manifest,data_dir=self.root,bt_mon=lambda:Native())
        return TestClient(app)

    def test_guest_and_active_readers_cannot_delete(self):
        for role,running,active in (("guest",False,False),("superman",True,False),("superman",False,True)):
            with self.subTest(role=role,running=running,active=active):
                result=self.make_client(role,running,active).post("/mgr/history/delete_preview",json={"id":self.identifier}).json()
                self.assertLess(result["result"],0)
        self.assertTrue((self.profile/"FUT_Data/his/min1/SHFE/rb2701.dsb").exists())

    def test_catalog_refresh_requests_fresh_coverage(self):
        with patch("wtpy.monitor.HistoryCatalog.dataset_entries",wraps=dataset_entries) as inspect:
            result=self.make_client().post("/mgr/history/catalog",json={"refresh":True}).json()
            self.assertEqual(result["entries"][0]["rows"],6)
            self.assertTrue(inspect.call_args.kwargs["refresh"])

    def test_preview_is_bound_to_object_and_rechecked(self):
        client=self.make_client()
        preview=client.post("/mgr/history/delete_preview",json={"id":self.identifier}).json()
        self.assertEqual(preview["result"],0)
        denied=client.post("/mgr/history/delete",json={"id":"../other","confirmation":preview["confirmation"]}).json()
        self.assertLess(denied["result"],0)
        preview=client.post("/mgr/history/delete_preview",json={"id":self.identifier}).json()
        result=client.post("/mgr/history/delete",json={"id":self.identifier,"confirmation":preview["confirmation"]}).json()
        self.assertEqual(result["result"],0)
        self.assertFalse((self.profile/"FUT_Data/his/min1/SHFE/rb2701.dsb").exists())

    def test_shared_storage_checks_every_collector_and_group(self):
        profiles=json.loads(self.manifest.read_text())
        profiles["groups"]["other"]=[dict(profiles["groups"]["g"][0],datahub_id="hub-other")]
        self.manifest.write_text(json.dumps(profiles))
        entries=dataset_entries(self.root)
        self.assertEqual({row["id"] for row in entries},
            {self.identifier,"local_other_test_SHFE_rb2701_m1"})
        for client in (self.make_client(running={"hub-other"}), self.make_client(products=["g"])):
            result=client.post("/mgr/history/delete_preview",json={"id":self.identifier}).json()
            self.assertLess(result["result"],0)
        allowed=self.make_client().post("/mgr/history/delete_preview",json={"id":self.identifier}).json()
        self.assertEqual(allowed["result"],0)


if __name__=="__main__":unittest.main()
