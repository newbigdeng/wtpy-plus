"""Bounded replay display and native-file pagination; no broker is started."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wtpy.monitor.PlusBacktest import PlusBacktestManager, write_json
from wtpy.monitor.PlusReport import equity_curve, read_view, save_view


class PlusReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.taskid = "a"*32
        self.folder = self.root/"tasks"/self.taskid
        self.folder.mkdir(parents=True)
        self.task = {"id":self.taskid,"user":"owner","status":"running","finance":{},"params":{}}
        write_json(self.folder/"task.json",self.task)
        self.manager=PlusBacktestManager(self.folder.parent,recover_running=False,cache_root=self.root/"cache")
        self.funds=[dict(date=20261019,time=90000000+i,trading_date=20261019,
            balance=10000+i,equity=10000+i,realized=0,unrealized=0,commission=0,
            margin=0,frozen_margin=0,frozen_commission=0,available=10000+i) for i in range(2500)]
        self.funds[1150]["equity"]=100
        self.funds[1151]["equity"]=90000
        write_json(self.folder/"report.json",dict(funds=self.funds,orders=[],trades=[],positions=[],events=[],rejections=[],
            summary={"equity":12499},route=["Historical Tick","TraderMocker"]))
        with (self.folder/"replay_funds.csv").open("w",newline="",encoding="utf-8") as stream:
            writer=csv.DictWriter(stream,fieldnames=list(self.funds[0]));writer.writeheader();writer.writerows(self.funds)

    def test_small_view_preserves_native_totals_and_curve_extremes(self):
        original=(self.folder/"report.json").read_bytes()
        with patch.object(self.manager,"capabilities",side_effect=AssertionError("detail must not scan datasets")):
            value=self.manager.detail("owner",self.taskid)
        self.assertEqual(value["ledger_totals"]["funds"],2500)
        self.assertLessEqual(len(value["funds"]),200)
        self.assertLessEqual(len(value["equity_curve"]),600)
        values=[row[1] for row in value["equity_curve"]]
        self.assertEqual((values[0],values[-1]),(10000,12499))
        self.assertIn(100,values);self.assertIn(90000,values)
        self.assertEqual((self.folder/"report.json").read_bytes(),original)
        self.assertFalse((self.folder/"report-view.json").exists())
        self.assertTrue((self.root/"cache"/self.taskid/"report-view.json").is_file())
        self.assertEqual(json.loads((self.folder/"task.json").read_text())["status"],"running")

    def test_native_funds_page_reads_requested_slice_and_final_row(self):
        self.manager.detail("owner",self.taskid)
        with patch("wtpy.monitor.PlusBacktest.read_json",wraps=__import__("wtpy.monitor.PlusBacktest",fromlist=["read_json"]).read_json) as reads:
            value=self.manager.detail("owner",self.taskid,ledger="funds",offset=2498,limit=200)
        self.assertEqual([r["equity"] for r in value["ledger_page"]["rows"]],[12498,12499])
        self.assertEqual(value["ledger_page"]["total"],2500)
        self.assertFalse(any(Path(call.args[0]).name=="report.json" for call in reads.call_args_list))

    def test_ownership_and_paging_are_checked_before_reading(self):
        with self.assertRaises(ValueError):self.manager.detail("other",self.taskid)
        self.assertFalse((self.root/"cache").exists())
        for options in [{"ledger":"unknown"},{"offset":-1},{"limit":1001}]:
            with self.subTest(options=options),self.assertRaises(ValueError):
                self.manager.detail("owner",self.taskid,**options)

    def test_cache_changes_with_source_and_log_is_explicit(self):
        first=self.manager.detail("owner",self.taskid)
        (self.folder/"runner.log").write_text("x"*20000+"tail",encoding="utf-8")
        self.assertEqual(first["log_tail"],"")
        value=self.manager.detail("owner",self.taskid,include_log=True)
        self.assertTrue(value["log_tail"].endswith("tail"))
        self.assertLessEqual(len(value["log_tail"]),12000)
        report=json.loads((self.folder/"report.json").read_text());report["summary"]["equity"]=7
        write_json(self.folder/"report.json",report)
        self.assertEqual(self.manager.detail("owner",self.taskid)["summary"]["equity"],7)

    def test_short_curve_uses_all_points(self):
        self.assertEqual([row[1] for row in equity_curve(self.funds[:3])],[10000,10001,10002])

    def test_cold_external_cache_reuses_valid_local_view_and_streams_native_performance(self):
        from wtpy.monitor.PlusBacktest import read_json
        source = self.folder/"report.json"
        report = read_json(source)
        report["summary"].update(capital=10000,account_source="TraderMocker原生账户")
        write_json(source,report)
        local_view = save_view(self.folder,report)
        local_view.pop("performance")
        write_json(self.folder/"report-view.json",local_view)
        original_view = (self.folder/"report-view.json").read_bytes()
        original_report = source.read_bytes()
        original_funds = (self.folder/"replay_funds.csv").read_bytes()
        def forbid_large_report(path, fallback=None):
            self.assertNotEqual(Path(path),source,"cold cache must reuse the validated small view")
            return read_json(path,fallback)
        with patch("wtpy.monitor.PlusBacktest.read_json",side_effect=forbid_large_report):
            value = read_view(self.folder,self.manager.cache_root/self.taskid)
        self.assertAlmostEqual(value["performance"]["total_return"],24.99)
        self.assertGreater(value["performance"]["max_falldown"],90)
        self.assertEqual((self.folder/"report-view.json").read_bytes(),original_view)
        self.assertEqual(source.read_bytes(),original_report)
        self.assertEqual((self.folder/"replay_funds.csv").read_bytes(),original_funds)
        cached = read_json(self.manager.cache_root/self.taskid/"report-view.json")
        self.assertEqual(cached["performance"],value["performance"])

    def test_cold_external_cache_rejects_stale_local_view(self):
        from wtpy.monitor.PlusBacktest import read_json
        source = self.folder/"report.json"
        report = read_json(source)
        save_view(self.folder,report)
        original_view = (self.folder/"report-view.json").read_bytes()
        report["summary"]["equity"] = 777777
        write_json(source,report)
        with patch("wtpy.monitor.PlusBacktest.read_json",wraps=read_json) as reads:
            value = self.manager.detail("owner",self.taskid)
        self.assertEqual(value["summary"]["equity"],777777)
        self.assertTrue(any(Path(call.args[0])==source for call in reads.call_args_list))
        self.assertEqual((self.folder/"report-view.json").read_bytes(),original_view)
        self.assertEqual(read_json(self.manager.cache_root/self.taskid/"report-view.json")["summary"]["equity"],777777)

    def test_report_replaced_during_cache_build_is_read_on_next_request(self):
        from wtpy.monitor.PlusBacktest import read_json
        source = self.folder/"report.json"
        def replace_after_read(path, fallback=None):
            value = read_json(path, fallback)
            if Path(path) == source:
                final = dict(value, summary={"equity":777777})
                write_json(source, final)
            return value
        with patch("wtpy.monitor.PlusBacktest.read_json", side_effect=replace_after_read):
            first = self.manager.detail("owner", self.taskid)
        self.assertEqual(first["summary"]["equity"], 12499)
        self.assertEqual(self.manager.detail("owner", self.taskid)["summary"]["equity"], 777777)

    def test_guest_never_receives_constructor_thresholds(self):
        from wtpy.monitor.GuestAccess import guest_data
        owner={"task":{"code":"SHFE.rb.2701","constructor_params":{"private_threshold":17},"params":{"private_threshold":17}},"summary":{"actual_trades":0}}
        guest=guest_data(owner)
        self.assertEqual(guest,{"task":{"code":"SHFE.rb.2701"},"summary":{"actual_trades":0}})
        self.assertEqual(owner["task"]["constructor_params"]["private_threshold"],17)


if __name__=="__main__":unittest.main()
