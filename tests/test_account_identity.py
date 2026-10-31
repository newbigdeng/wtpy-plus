"""Identity-scoped history migration and capital reset ownership."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote

from wtpy.monitor.WorkbenchApi import WorkbenchFeed


class AccountIdentityTests(unittest.TestCase):
    now=1792900000000

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="wt-account-identity-")
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.group=self.root/"profile";self.group.mkdir()
        self.feed=WorkbenchFeed(self.root/"history")
        self.legacy=self.feed.history_root/"g"/(quote(str(self.group.resolve()),safe="")+".json")
        self.legacy.parent.mkdir(parents=True)
        self.v3=self.legacy.with_suffix(".accounts-v3.json")
        self.configure("account-a",self.now-10000)

    def configure(self,user,stamp):
        path=self.group/"config.json"
        path.write_text(json.dumps({"traders":[{"id":"mock","broker":"test","user":user}]}))
        os.utime(path,(stamp/1000,)*2)

    def account(self,equity,stamp):
        return dict(channel="mock",currency="CNY",equity=equity,updated_at=stamp,
                    timestamp_source="柜台查询回执时间",age_seconds=0,valid=True,deposit=0,withdraw=0)

    def sample(self,equity,stamp):
        return self.feed.account_series("g",[self.account(equity,stamp)],self.group)[0]

    def reset(self,capital,stamp):
        path=self.legacy.with_suffix(".capital-events.json")
        value=json.loads(path.read_text()) if path.exists() else {"mock:CNY":[]}
        value["mock:CNY"].append(dict(type="simnow_reset",time=stamp,confirmed_at=stamp,requested_capital=capital))
        path.write_text(json.dumps(value))

    def old_history(self,anchor_user):
        value={"schema":2,"accounts":{"mock:CNY":[{"time":self.now,"equity":10000}]},"legacy_balance":{}}
        self.legacy.write_text(json.dumps(value));self.reset(10000,self.now-1000)
        folder=self.group/"generated";folder.mkdir()
        (folder/"account-display-snapshots.json").write_text(json.dumps({"accounts":{"mock:CNY":{
            "identity":["test",anchor_user],"account":{"updated_at":self.now}}}}))

    def test_matching_identity_anchor_migrates_without_rewriting_old_history(self):
        self.old_history("account-a");before=self.legacy.read_bytes()
        account=self.sample(10500,self.now+1)
        self.assertEqual([point["equity"] for point in account["history"]],[10000,10500])
        self.assertEqual(account["performance"]["pnl"],500)
        self.assertEqual(self.legacy.read_bytes(),before)
        self.configure("account-a",self.now+5000)
        self.assertEqual(self.sample(10600,self.now+2)["performance"]["pnl"],600)

    def test_unmatched_identity_keeps_old_records_unbound_and_rejects_old_reset(self):
        self.old_history("another-account")
        account=self.sample(50000,self.now+1)
        self.assertFalse(account["performance"]["valid"])
        self.assertEqual(account["unbound_history_samples"],1)
        data=json.loads(self.v3.read_text())
        self.assertEqual(data["unbound_accounts"]["mock:CNY"][0]["equity"],10000)
        self.assertEqual(data["unbound_capital_events"]["mock:CNY"][0]["requested_capital"],10000)

    def test_switch_account_does_not_borrow_previous_reset_or_curve(self):
        self.sample(10000,self.now);self.reset(10000,self.now+1)
        self.sample(10500,self.now+1000)
        self.configure("account-b",self.now+2000)
        account=self.sample(50000,self.now+3000)
        self.assertFalse(account["performance"]["valid"])
        self.assertEqual([point["equity"] for point in account["history"]],[50000])
        self.assertEqual(len(json.loads(self.v3.read_text())["books"]),2)

    def test_switch_back_restores_own_history_and_ignores_other_accounts_reset(self):
        self.sample(10000,self.now);self.reset(10000,self.now+1)
        self.sample(10500,self.now+1000)
        self.configure("account-b",self.now+2000)
        self.sample(50000,self.now+3000);self.reset(50000,self.now+4000)
        self.sample(51000,self.now+4500)
        self.configure("account-a",self.now+5000)
        account=self.sample(11000,self.now+6000)
        self.assertEqual([point["equity"] for point in account["history"]],[10000,10500,11000])
        self.assertEqual(account["performance"]["pnl"],1000)
        self.assertEqual([event["requested_capital"] for event in account["capital_events"]],[10000])


if __name__=="__main__":
    unittest.main()
