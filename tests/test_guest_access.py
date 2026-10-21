"""Exercise the public guest boundary through the actual ASGI application."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from wtpy.monitor.DataMgr import DataMgr
from wtpy.monitor.WtMonSvr import WtMonSvr, check_auth
from wtpy.monitor.GuestAccess import GUEST_READ_PATHS, LoginAttempts
from wtpy.monitor.WtBtMon import WtBtMon


class GuestAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-guest-")
        self.addCleanup(self.temp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.cwd)
        root = Path(self.temp.name)
        (root / "static/console").mkdir(parents=True)
        (root / "static/mobile").mkdir(parents=True)
        self.password = secrets.token_urlsafe(18)
        self.digest = hashlib.md5(self.password.encode()).hexdigest()
        manager = DataMgr("data.db")
        self.addCleanup(manager.get_db().close)
        manager.add_user({"loginid": "owner-test", "name": "Owner", "role": "admin",
                          "passwd": self.digest, "iplist": "", "products": "", "remark": ""}, "setup")
        env = patch.dict(os.environ, {"WT_GUEST_ENABLED": "1", "WT_MONITOR_OWNER": "owner-test",
            "WT_STRATEGY_LIBRARY_ROOT": str(root/"strategy-library"),
            "WT_RISK_LIBRARY": str(root/"risk-library"),
            "WT_CHANNEL_PROFILES": str(root/"channel-profiles.json")})
        env.start(); self.addCleanup(env.stop)
        self.server = WtMonSvr(static_folder=str(root / "static"))
        self.addCleanup(self.server.__data_mgr__.get_db().close)
        self.server.set_bt_mon(WtBtMon(str(root / "research")))
        self.strategy = self.server.__bt_mon__.add_strategy("owner-test", "Existing example", "cta")
        self.strategy_id = self.strategy["id"]
        manager = self.server.__bt_mon__
        manager.user_bts.setdefault("owner-test", {})["finished-native"] = {"id": "finished-native", "straid": self.strategy_id, "status": "completed", "perform": {"total_return": 0.12}}
        output = Path(manager.path) / "owner-test" / self.strategy_id / "backtests/finished-native"
        output.mkdir(parents=True)
        (output/"task.log").write_text("private native log " + self.password)
        plus = self.server.__plus_mon__
        plus_id = "a" * 32
        folder = plus.root/plus_id
        folder.mkdir(parents=True)
        (folder/"task.json").write_text(json.dumps({"id": plus_id, "user": "owner-test", "straid": self.strategy_id, "status": "completed", "created_at": 1}))
        (folder/"report.json").write_text(json.dumps({"summary": {"equity": 123456}, "funds": [{"equity": 123456}]}))
        (folder/"runner.log").write_text("private plus log " + self.password)
        self.client = TestClient(self.server.app)
        self.addCleanup(self.client.close)

    def guest(self):
        result = self.client.post("/mgr/guest", json={})
        self.assertEqual(result.json()["userinfo"]["role"], "guest")
        self.assertIn("httponly", result.headers["set-cookie"].lower())
        self.assertIn("samesite=strict", result.headers["set-cookie"].lower())

    def owner(self):
        result = self.client.post("/mgr/login", json={"loginid": "owner-test", "passwd": self.digest})
        self.assertEqual(result.json()["result"], 0)
        self.assertNotIn("passwd", result.text)

    def test_anonymous_and_guest_metadata(self):
        self.assertEqual(self.client.post("/mgr/qrygrp", json={}).status_code, 401)
        self.guest()
        session = self.client.post("/mgr/session", json={}).json()
        self.assertEqual(session["userinfo"]["role"], "guest")
        self.assertEqual(self.client.post("/mgr/qrygrp", json={}).json()["result"], 0)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)

    def test_new_library_routes_are_read_only_and_hide_uploaded_source(self):
        self.owner()
        source = 'from wtpy import BaseCtaStrategy\nclass TestLibrary(BaseCtaStrategy):\n    def __init__(self, name, code="private_code"): super().__init__(name)\n'
        saved = self.client.post("/mgr/strategy_library_save", json={"library_id": "visible_strategy",
            "name": "Visible strategy", "description": "", "source": source}).json()
        self.assertEqual(saved["result"], 0, saved)
        self.guest()
        for path in ("/mgr/strategy_library", "/mgr/history/catalog", "/mgr/history/datasets", "/mgr/risk-library/list"):
            response = self.client.post(path, json={})
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.json()["result"], 0, response.text)
        view = self.client.post("/mgr/strategy_library_get", json={"library_id": "visible_strategy", "version": 1})
        self.assertNotIn("private_code", view.text)
        self.assertNotIn("source", view.json()["strategy"])
        self.assertNotIn("parameters", view.json()["strategy"])
        from wtpy.monitor.GuestAccess import guest_data
        self.assertEqual(guest_data({"market_snapshot": {"files": ["/private/input.dsb"]}, "code": "SHFE.rb.2701"}),
                         {"code": "SHFE.rb.2701"})

    def test_backtest_dataset_cannot_bypass_group_scope(self):
        user = {"loginid": "owner-test", "role": "admin", "products": ["allowed"]}
        hidden = {"id": "local_hidden", "groupid": "other", "code": "SHFE.rb.2701"}
        native = self.server.__bt_mon__
        plus = self.server.__plus_mon__
        with patch("wtpy.monitor.WtMonSvr.check_auth", return_value=(True, user)), \
             patch("wtpy.monitor.HistoryCatalog.dataset_entries", return_value=[hidden]), \
             patch.object(plus, "capabilities", return_value={"engines": ["CTA"], "datasets": [hidden]}), \
             patch.object(native, "run_backtest") as run_native, patch.object(plus, "create") as run_plus:
            self.owner()
            self.assertEqual(self.client.post("/bt/plus/capabilities", json={}).json()["datasets"], [])
            native_result = self.client.post("/bt/runstrabt", json={"straid": self.strategy_id,
                "dataset": hidden["id"], "stime": 20261019, "etime": 20261020}).json()
            plus_result = self.client.post("/bt/plus/run", json={"straid": self.strategy_id,
                "dataset": hidden["id"], "code": hidden["code"]}).json()
            self.assertEqual(native_result["result"], -403, native_result)
            self.assertEqual(plus_result["result"], -403, plus_result)
            run_native.assert_not_called()
            run_plus.assert_not_called()

    def test_every_non_read_route_rejected_before_validation(self):
        self.guest()
        checked = 0
        for route in self.server.app.routes:
            path = getattr(route, "path", "")
            if not path.startswith(("/mgr/", "/bt/")) or path in GUEST_READ_PATHS or path in ("/mgr/login", "/mgr/access", "/mgr/guest", "/mgr/logout"):
                continue
            response = self.client.post(path, json={"role": "admin", "loginid": "owner-test", "token": "fake-admin"})
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.json()["code"], "GUEST_READ_ONLY", path)
            checked += 1
        self.assertGreater(checked, 20)
        for path in ("/mgr/new-future-route", "/bt/new-future-route"):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post("/mgr/market/providers", json={"refresh": True}).status_code, 403)

    def test_owner_results_readable_but_source_and_credentials_private(self):
        self.guest()
        listing = self.client.post("/bt/qrystras", json={}).json()
        self.assertEqual(listing["result"], 0)
        self.assertTrue(listing["strategies"])
        self.assertEqual(listing["strategies"][0]["name"], "Existing example")
        code = self.client.post("/bt/qrycode", json={"straid": self.strategy}).json()
        self.assertIn("不公开策略源码", code["content"])
        private = self.client.post("/mgr/qrygrpfile", json={"path": "/data/market-providers.local.json"}).text
        self.assertNotIn(self.digest, private)
        self.assertNotIn("market-providers", private)
        users = self.client.post("/mgr/qryusers", json={}).text
        self.assertNotIn("owner-test", users)
        self.assertNotIn(self.digest, users)
        native = self.client.post("/bt/qrybtdetail", json={"straid": self.strategy_id, "btid": "finished-native"}).json()
        self.assertEqual(native["status"], "completed")
        self.assertNotIn("logs", native)
        self.assertNotIn(self.password, json.dumps(native))
        plus = self.client.post("/bt/plus/detail", json={"taskid": "a" * 32}).json()
        self.assertEqual(plus["summary"]["equity"], 123456)
        self.assertNotIn("log_tail", plus)
        self.assertNotIn(self.password, json.dumps(plus))

    def test_fixed_owner_scope_and_sanitized_errors(self):
        from fastapi import Request
        @self.server.app.post("/bt/qryanalyst")
        def probe(request: Request):
            ok, user = check_auth(request, "forged-owner")
            return {"result": 0, "scope": user["loginid"], "role": user["role"], "passwd": self.digest,
                    "nested": {"token": "private", "path": "/data/private", "storage": "/data/private", "log_tail": "unsafe private log", "can_operate": True}}
        self.guest()
        value = self.client.post("/bt/qryanalyst", json={"ownerid": "other", "role": "admin"}).json()
        self.assertEqual(value["scope"], "owner-test")
        self.assertEqual(value["role"], "guest")
        self.assertEqual(value["nested"], {"can_operate": False})
        self.assertNotIn("passwd", value)

    def test_public_header_can_only_reduce_owner_access(self):
        headers = {"X-WT-Public-Read-Only": "1"}
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/console/")
        self.assertTrue(self.client.post("/mgr/access", json={}, headers=headers).json()["public_read_only"])
        self.assertFalse(self.client.post("/mgr/access", json={}).json()["public_read_only"])
        self.assertEqual(self.client.post("/mgr/login", json={"loginid": "owner-test", "passwd": self.digest}, headers=headers).status_code, 403)
        self.owner()
        self.assertEqual(self.client.post("/mgr/qrygrp", json={}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/mgr/qrygrp", json={}).status_code, 200)
        self.guest()
        self.assertEqual(self.client.post("/mgr/qrygrp", json={}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/mgr/startapp", json={}, headers=headers).status_code, 403)

    def test_owner_writes_preserved_and_account_creation_disabled(self):
        self.owner()
        # Missing body reaches validation for owner; guest is refused before it.
        self.assertEqual(self.client.post("/mgr/startapp", json={}).status_code, 422)
        value = self.client.post("/mgr/cmtuser", json={"loginid": "new", "name": "New", "role": "admin"}).json()
        self.assertEqual(value["result"], -403)
        cross = self.client.post("/mgr/startapp", json={}, headers={"Origin": "https://other.example"})
        self.assertEqual(cross.status_code, 403)
        self.client.post("/mgr/logout", json={})
        self.assertEqual(self.client.post("/mgr/qrygrp", json={}).status_code, 401)

    def test_invalid_guest_cookie_cannot_be_promoted(self):
        import base64
        self.guest()
        cookie = self.client.cookies.get("WtMonSvr_sid")
        parts = cookie.split(".")
        payload = json.loads(base64.b64decode(parts[0]))
        payload["tokeninfo"]["role"] = "admin"
        parts[0] = base64.b64encode(json.dumps(payload).encode()).decode()
        self.client.cookies.clear()
        self.client.cookies.set("WtMonSvr_sid", ".".join(parts))
        self.assertEqual(self.client.post("/mgr/startapp", json={}).status_code, 401)

    def test_websocket_anonymous_cross_origin_and_guest_expiry(self):
        self.server.push_svr.run()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/"): pass
        self.guest()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/", headers={"origin": "https://other.example"}): pass
        with self.client.websocket_connect("/") as socket:
            socket.send_json({"type": "subscribe", "groupid": "read-only-group"})
            self.assertEqual(socket.receive_json()["groupid"], "read-only-group")
            socket.send_json({"type": "heartbeat"})
            self.assertEqual(socket.receive_json()["message"], "pong")
            self.server.app.state.auth_version = secrets.token_urlsafe(24)
            socket.send_json({"type": "heartbeat"})
            with self.assertRaises(WebSocketDisconnect): socket.receive_json()

    def test_login_rate_limit_and_disabled_guest(self):
        for _ in range(8):
            self.assertLess(self.client.post("/mgr/login", json={"loginid": "wrong", "passwd": "wrong"}).json()["result"], 0)
        self.assertEqual(self.client.post("/mgr/login", json={"loginid": "wrong", "passwd": "wrong"}).status_code, 429)
        attempts = LoginAttempts()
        for _ in range(80): attempts.record("same-peer", False)
        for number in range(2000): self.assertTrue(attempts.blocked("new-" + str(number)))
        self.assertEqual(len(attempts.by_peer), 1)
        self.server.app.state.guest_enabled = False
        self.assertEqual(self.client.post("/mgr/guest", json={}).json()["result"], -403)

if __name__ == "__main__":
    unittest.main()
