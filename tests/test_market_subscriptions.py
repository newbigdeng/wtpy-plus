import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from wtpy.monitor.MarketSubscriptions import MarketSubscriptions


class GroupStore:
    def __init__(self, root):
        self.group = {"id": "g", "path": str(root / "run")}

    def get_group(self, groupid):
        return self.group

    def get_groups(self):
        return [self.group]


class Processes:
    def __init__(self, root):
        self.running = {"hub": True}
        self.actions = []
        self.folder = root / "datahub"

    def isRunning(self, appid):
        return self.running.get(appid, False)

    def getAppConf(self, appid):
        return {"folder": str(self.folder), "param": "-u " + str(self.folder / "run_datahub.py")}

    def restart(self, appid):
        self.actions.append(("restart", appid))
        self.running[appid] = True


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wt-subscriptions-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        hub = self.root / "datahub"
        hub.mkdir()
        common = self.root / "common"
        common.mkdir()
        (self.root / "run").mkdir()
        (hub / "dtcfg.ctp.yaml").write_text(yaml.safe_dump({
            "parsers": "mdparsers.ctp.yaml", "basefiles": {
                "contract": "../common/contracts.json", "commodity": "../common/commodities.json",
                "session": "../common/sessions.json"}}))
        (common / "contracts.json").write_text(json.dumps({"SHFE": {
            "rb2701": {"name": "rb", "product": "rb"},
            "cu2701": {"name": "cu", "product": "cu"}}}))
        (common / "commodities.json").write_text(json.dumps({"SHFE": {
            "rb": {"session": "NIGHT"}, "cu": {"session": "NIGHT"}}}))
        self.path = hub / "mdparsers.ctp.yaml"
        self.config = {"parsers": [{"id": "ctp", "active": True, "module": "ParserCTP",
                                    "code": "SHFE.rb2701", "filter": "SHFE",
                                    "user": "0000123", "pass": "test-private-value",
                                    "custom": {"untouched": True}}], "other": 123}
        self.path.write_text(yaml.safe_dump(self.config))
        manifest = self.root / "profiles.json"
        manifest.write_text(json.dumps({"groups": {"g": [{
            "id": "sim1", "label": "Sim 1", "root": str(self.root), "datahub_id": "hub"}]}}))
        self.processes = Processes(self.root)
        self.manager = MarketSubscriptions(GroupStore(self.root), self.processes, manifest)

    def test_explicit_contract_change_preserves_private_config_and_never_starts_engine(self):
        result = self.manager.apply("g", None, ["SHFE.cu2701"], False)
        actual = yaml.safe_load(self.path.read_text())
        expected = self.config.copy()
        expected["parsers"] = [dict(self.config["parsers"][0], code="SHFE.cu2701", filter="")]
        self.assertEqual(actual, expected)
        self.assertEqual(self.processes.actions, [])
        self.assertTrue(result["restart_required"])
        self.assertFalse(result["engine_started"])
        # A later explicit restart must work even though the saved selection is unchanged.
        result = self.manager.apply("g", None, ["SHFE.cu2701"], True)
        self.assertEqual(self.processes.actions, [("restart", "hub")])
        self.assertTrue(result["collector_restarted"])

    def test_native_all_contract_fallback_is_never_written(self):
        for selection in ([], ["SHFE.rb"], ["SHFE.unknown2701"]):
            with self.subTest(selection=selection):
                original = self.path.read_text()
                with self.assertRaises(ValueError):
                    self.manager.apply("g", None, selection, True)
                self.assertEqual(self.path.read_text(), original)
        self.assertEqual(self.processes.actions, [])

    def test_running_engine_blocks_disruption(self):
        self.processes.running["g"] = True
        original = self.path.read_text()
        with self.assertRaises(ValueError):
            self.manager.apply("g", None, ["SHFE.cu2701"], True)
        self.assertEqual(self.path.read_text(), original)
        self.assertEqual(self.processes.actions, [])

    def test_native_filter_precedence_and_no_credential_return(self):
        self.config["parsers"][0].pop("code")
        self.path.write_text(yaml.safe_dump(self.config))
        snapshot = self.manager.snapshot("g")
        self.assertEqual(snapshot["selected"], ["SHFE.cu2701", "SHFE.rb2701"])
        self.assertEqual(snapshot["selection_mode"], "exchanges")
        self.assertNotIn("test-private-value", json.dumps(snapshot))
        self.assertFalse(snapshot["active_subscriptions_confirmed"])

    def test_native_master_refresh_preserves_subscriptions_and_known_session_rules(self):
        common = self.root / "common"
        (common / "sessions.json").write_text(json.dumps({"NIGHT": {"sections": []}}))
        (common / "commodity-reference.json").write_text(json.dumps({"SHFE": {
            "rb": {"session": "NIGHT"}, "cu": {"session": "NIGHT", "custom_fee": 3}}}))
        (self.root / "run/config.ctp-observe.yaml").write_text(yaml.safe_dump({"traders": "traders.yaml"}))
        (self.root / "run/traders.yaml").write_text(yaml.safe_dump({"traders": [{
            "active": True, "module": "TraderCTP", "front": "tcp://test", "broker": "test",
            "user": "test", "pass": "test-private-value", "appid": "test", "authcode": "test"}]}))
        queried = {"SHFE": {"cu2702": {"product": "cu", "code": "cu2702"}},
                   "GFEX": {"si2702": {"product": "si", "code": "si2702"}}}
        commodities = {"SHFE": {"cu": {"pricetick": 10, "volscale": 5, "session": ""}},
                       "GFEX": {"si": {"pricetick": 5, "volscale": 5}}}
        original = self.path.read_text()
        with patch("wtpy.monitor.MarketSubscriptions._ctp_library", return_value=self.root / "test.so"), \
             patch("wtpy.monitor.MarketSubscriptions._query_native_contracts", return_value=(queried, commodities)):
            result = self.manager.refresh("g")
        refreshed = json.loads((common / "commodities.json").read_text())
        self.assertEqual(refreshed["SHFE"]["cu"], {"session": "NIGHT", "pricetick": 10, "volscale": 5, "custom_fee": 3})
        self.assertEqual(result["skipped_products"], ["GFEX.si"])
        self.assertEqual(result["updated_contracts"], 1)
        self.assertFalse(result["subscriptions_changed"])
        self.assertEqual(self.path.read_text(), original)
        self.assertEqual(self.processes.actions, [])


if __name__ == "__main__":
    unittest.main()
