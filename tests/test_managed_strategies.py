"""Publication, privilege and ledger checks without a broker connection."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from wtpy.monitor.DataMgr import DataMgr
from wtpy.monitor.ManagedStrategies import analyze, _normalize
from wtpy.monitor.ManagedStrategyRunner import native_config, register_plan
from wtpy.monitor.WtMonSvr import WtMonSvr

SOURCE = '''from wtpy import BaseCtaStrategy
from pathlib import Path
Path('should-not-execute').touch()
class ImportedCta(BaseCtaStrategy):
 def __init__(self,name,code,period='m1',lots=1):
  super().__init__(name);self.code=code;self.lots=lots
 def on_init(self,context): context.stra_sub_ticks(self.code)
'''


class ManagedStrategyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-managed-")
        self.addCleanup(self.temp.cleanup)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.temp.name)
        self.root = Path(self.temp.name)
        (self.root / "static/console").mkdir(parents=True)
        (self.root / "static/mobile").mkdir(parents=True)
        digest = hashlib.md5(secrets.token_bytes(24)).hexdigest()
        manager = DataMgr("data.db")
        manager.add_user({"loginid": "owner", "name": "Owner", "role": "admin", "passwd": digest,
                          "iplist": "", "products": "", "remark": ""}, "setup")
        manager.get_db().close()
        env = patch.dict(os.environ, {"WT_GUEST_ENABLED": "1", "WT_MONITOR_OWNER": "owner",
            "WT_MANAGED_GROUP_ROOT": str(self.root / "managed")})
        env.start(); self.addCleanup(env.stop)
        self.server = WtMonSvr(static_folder=str(self.root / "static"), deploy_dir=str(self.root))
        self.addCleanup(self.server._dog.shutdown)
        self.addCleanup(self.server.__data_mgr__.get_db().close)
        self.client = TestClient(self.server.app)
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post("/mgr/login", json={"loginid": "owner", "passwd": digest}).json()["result"], 0)
        folder = self.root / "legacy"
        folder.mkdir()
        (folder / "run.py").write_text("# original observation entry\n")
        self.base = {"env": {"name": "cta"}, "parsers": [], "executers": [],
            "traders": [{"active": True, "id": "paper", "module": "TraderMocker", "pass": "private-secret", "flowdir": str(folder / "old-flow")}],
            "basefiles": {}, "data": {"store": {"path": "../history"}}}
        (folder / "config.json").write_text(json.dumps(self.base))
        (folder / "acceptance.once").touch()
        (folder / "generated/stradata").mkdir(parents=True)
        (folder / "generated/stradata/old.json").write_text(json.dumps({"positions": [{"volume": 0}]}))
        group = {"id": "legacy", "name": "Legacy", "path": str(folder), "info": "", "gtype": "cta",
                 "datmod": "mannual", "env": "product", "mqurl": ""}
        self.server.__data_mgr__.add_group(group)
        self.appconf = {"id": "legacy", "path": os.sys.executable, "folder": str(folder),
            "param": "-u " + str(folder / "run.py"), "type": 1, "span": 3, "guard": False,
            "redirect": False, "mqurl": "", "schedule": {"active": False, "weekflag": "0000000",
            "tasks": [{"active": False, "time": 0, "action": 0} for _ in range(6)]}}
        self.server._dog.applyAppConf(self.appconf, True)

    def post(self, path, **value):
        return self.client.post("/mgr/" + path, json=value).json()

    def plan(self, groupid="legacy", execution="twap"):
        plan = self.post("strategy_plan", groupid=groupid)["plan"]
        plan["strategies"] = [{"id": "trend", "source": SOURCE, "class_name": "ImportedCta", "enabled": True,
            "params": {"code": "SHFE.rb.2701"}, "trader": "paper", "execution": {"id": execution}}]
        return plan

    def test_import_is_static_and_parameters_are_checked(self):
        result = self.post("analyze_strategy", groupid="legacy", source=SOURCE)
        self.assertEqual(result["classes"][0]["name"], "ImportedCta")
        self.assertFalse((self.root / "should-not-execute").exists())
        self.assertEqual(analyze(SOURCE)[0]["parameters"][2]["default"], "m1")
        sel = SOURCE.replace("BaseCtaStrategy", "BaseSelStrategy")
        self.assertFalse(analyze(sel)[0]["supported"])
        rejected = self.post("create_strategy_group", groupid="selection", name="SEL", template_groupid="legacy", engine_type="SEL")
        self.assertIn("支持CTA和HFT", rejected["message"])
        plan = self.plan()
        plan["strategies"][0]["params"] = {}
        self.assertIn("缺少策略参数", self.post("save_strategy_plan", groupid="legacy", plan=plan)["message"])
        self.assertFalse((self.root / "should-not-execute").exists())

    def test_draft_publish_routing_and_explicit_takeover(self):
        result = self.post("save_strategy_plan", groupid="legacy", plan=self.plan())
        self.assertEqual(result["result"], 0)
        self.assertFalse(result["managed"])
        self.assertEqual(self.server._dog.getAppConf("legacy")["param"], self.appconf["param"])
        rejected = self.post("save_strategy_plan", groupid="legacy", plan=result["plan"], apply=True)
        self.assertIn("确认接管", rejected["message"])
        result = self.post("save_strategy_plan", groupid="legacy", plan=result["plan"], apply=True, confirm_takeover=True)
        self.assertEqual(result["result"], 0)
        self.assertTrue(result["managed"])
        self.assertFalse(result["running"])
        self.assertFalse(result["plan"]["trading_enabled"])
        rejected = self.post("startapp", appid="legacy")
        self.assertIn("尚未启用交易授权", rejected["message"])
        self.assertIn("尚未启用交易授权", self.post("startgrp", groupid="legacy")["message"])
        self.assertFalse(self.server._dog.isRunning("legacy"))
        self.assertEqual((self.root / "legacy/run.py").read_text(), "# original observation entry\n")
        revision = self.root / "legacy/managed-strategies/revisions" / result["applied_revision"]
        config = json.loads((revision / "config.json").read_text())
        self.assertEqual(config["routers"], [{"strategy": "trend", "executer": "managed-trend"}])
        self.assertEqual(config["executers"][0]["trader"], "paper")
        self.assertEqual(config["executers"][0]["policy"]["default"]["name"], "WtExeFact.WtTWapExeUnit")
        self.assertFalse((self.root / "should-not-execute").exists())
        child = subprocess.run([os.sys.executable, str(self.root / "legacy/run_managed.py")],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
        self.assertNotEqual(child.returncode, 0)
        self.assertIn("尚未启用交易授权", child.stderr)
        self.assertFalse((self.root / "should-not-execute").exists())
        # User modules run in the strategy child, after publication, not in HTTP.
        class RecordingEngine:
            def add_cta_strategy(inner, strategy, slippage=0): inner.strategy = strategy
        engine = RecordingEngine()
        register_plan(engine, result["plan"], revision)
        self.assertEqual(engine.strategy.name(), "trend")
        self.assertTrue((self.root / "should-not-execute").exists())

    def test_new_group_is_isolated_and_has_no_authorization(self):
        result = self.post("create_strategy_group", groupid="isolated", name="Isolated", template_groupid="legacy", engine_type="CTA")
        self.assertEqual(result["result"], 0)
        folder = self.root / "managed/isolated"
        self.assertFalse((folder / "acceptance.once").exists())
        self.assertFalse((folder / "generated").exists())
        self.assertFalse(result["plan"]["trading_enabled"])
        self.assertEqual(result["plan"]["strategies"], [])
        self.assertIn("先在策略管理", self.post("startapp", appid="isolated")["message"])
        conf = self.server._dog.getAppConf("isolated")
        self.assertFalse(conf["guard"] or conf["schedule"]["active"])
        self.assertFalse(self.server._dog.isRunning("isolated"))
        cloned = json.loads((folder / "config.json").read_text())
        self.assertEqual(cloned["env"]["filters"], str(folder / "filters.json"))
        self.assertTrue(Path(cloned["traders"][0]["flowdir"]).is_relative_to(folder))
        self.assertTrue(Path(cloned["traders"][0]["flowdir"]).is_dir())

    def test_running_guard_and_held_ledger_prevent_changes(self):
        with patch.object(self.server._dog, "is_process_running", return_value=True):
            result = self.post("save_strategy_plan", groupid="legacy", plan=self.plan())
            self.assertIn("先停止", result["message"])
        conf = dict(self.appconf, guard=True)
        self.server._dog.applyAppConf(conf, True)
        self.assertIn("关闭进程守护", self.post("save_strategy_plan", groupid="legacy", plan=self.plan())["message"])
        self.server._dog.applyAppConf(self.appconf, True)
        result = self.post("save_strategy_plan", groupid="legacy", plan=self.plan(), apply=True, confirm_takeover=True)
        self.assertEqual(result["result"], 0)
        ledger = self.root / "legacy/generated/stradata/trend.json"
        ledger.write_text(json.dumps({"positions": [{"volume": 2}]}))
        plan = result["plan"]
        plan["strategies"][0]["params"]["lots"] = 2
        result = self.post("save_strategy_plan", groupid="legacy", plan=plan)
        self.assertIn("仍有持仓", result["message"])

    def test_os_scan_detects_manual_process_without_scheduler_running_state(self):
        entry = self.root / "legacy/run.py"
        entry.write_text("import time\ntime.sleep(30)\n")
        process = subprocess.Popen([os.sys.executable, "run.py"], cwd=entry.parent)
        try:
            self.assertFalse(self.server._dog.isRunning("legacy"))
            self.assertTrue(self.server._dog.is_process_running("legacy"))
            result = self.post("save_strategy_plan", groupid="legacy", plan=self.plan())
            self.assertIn("先停止", result["message"])
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_hft_checks_native_channel_positions_and_latest_trade_snapshot(self):
        result = self.post("create_strategy_group", groupid="fast", name="HFT", template_groupid="legacy", engine_type="HFT")
        plan = result["plan"]
        plan["strategies"] = [{"id": "fastone", "source": SOURCE.replace("BaseCtaStrategy", "BaseHftStrategy"),
            "params": {"code": "SHFE.rb.2701"}, "trader": "paper"}]
        result = self.post("save_strategy_plan", groupid="fast", plan=plan, apply=True, confirm_takeover=True)
        self.assertEqual(result["result"], 0)
        plan = result["plan"]
        plan["strategies"][0]["params"]["lots"] = 2
        folder = self.root / "managed/fast/generated/traders/paper"
        folder.mkdir(parents=True)
        snapshot = folder / "rtdata.json"
        snapshot.write_text(json.dumps({"positions": [{"long": {"prevol": 2}, "short": {"newvol": 0}}]}))
        result = self.post("save_strategy_plan", groupid="fast", plan=plan)
        self.assertIn("HFT通道paper仍有持仓", result["message"])
        snapshot.write_text(json.dumps({"positions": []}))
        trades = folder / "trades.csv"
        trades.write_text("localid,date,time,code,action,volume,price,tradeid,orderid\n")
        after = snapshot.stat().st_mtime_ns + 1000000
        os.utime(trades, ns=(after, after))
        result = self.post("save_strategy_plan", groupid="fast", plan=plan)
        self.assertIn("快照后还有委托成交", result["message"])

    def test_guest_reads_plan_without_private_source_and_cannot_write(self):
        result = self.post("save_strategy_plan", groupid="legacy", plan=self.plan())
        self.assertEqual(result["result"], 0)
        self.assertEqual(self.post("guest")["result"], 0)
        response = self.client.post("/mgr/strategy_plan", json={"groupid": "legacy"})
        self.assertNotIn("source", response.json()["plan"]["strategies"][0])
        self.assertNotIn("private-secret", response.text)
        self.assertFalse(response.json()["can_manage"])
        catalog = self.post("strategy_catalog", groupid="legacy")
        self.assertEqual(catalog["channels"], [{"id": "paper", "label": "paper"}])
        for path in ("save_strategy_plan", "analyze_strategy", "create_strategy_group"):
            response = self.client.post("/mgr/" + path, json={})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "GUEST_READ_ONLY")


if __name__ == "__main__":
    unittest.main()
