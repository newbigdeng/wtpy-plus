"""Focused rule/version, context-gate and isolated native replay checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from wtpy.monitor.RiskLibrary import delete_rule, resolve_selection, save_rule, validate_fast_selection
from wtpy.monitor.RiskRuntime import RiskRuntime, read_risk_events, live_account_provider


def rule(kind="max_position_lots", limit=3, mode="pretrade", identifier="position"):
    return dict(id=identifier, name=identifier, type=kind, mode=mode, params={"limit": limit}, version=1)


class Strategy:
    def name(self):
        return "example"

    def on_tick(self, ctx, method, *args, **kwargs):
        return getattr(ctx, method)(*args, **kwargs)

    def on_order(self, ctx, *args):
        pass

    def on_entrust(self, ctx, *args):
        pass


class Context:
    def __init__(self, position=0):
        self.position, self.calls, self.sequence = position, [], 0

    def stra_get_position(self, code):
        return self.position

    def stra_set_position(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        self.calls.append(("set", qty, limitprice, stopprice))
        self.position = qty

    def stra_enter_long(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        self.calls.append(("long", qty))
        self.position = max(0, self.position)+qty

    def stra_enter_short(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        self.calls.append(("short", qty))
        self.position = min(0, self.position)-qty

    def stra_exit_long(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        self.calls.append(("exit_long", qty))
        self.position = max(0, self.position-qty)

    def stra_exit_short(self, stdCode, qty, usertag="", limitprice=0, stopprice=0):
        self.calls.append(("exit_short", qty))
        self.position = min(0, self.position+qty)

    def stra_buy(self, stdCode, price, qty, userTag="", flag=0):
        self.sequence += 1
        self.calls.append(("buy", qty))
        return [self.sequence]

    def stra_sell(self, stdCode, price, qty, userTag="", flag=0):
        self.sequence += 1
        self.calls.append(("sell", qty))
        return [self.sequence]


class RiskRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-risk-rules-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.account = dict(valid=True, equity=100000, available=50000, prebalance=100000, trading_date=20261020)

    def wrap(self, rules, kind="CTA"):
        runtime = RiskRuntime({"rules": rules}, self.root/"events.jsonl", lambda *_: dict(self.account))
        return runtime.wrap(Strategy(), kind)

    def test_versions_are_frozen_and_retirement_does_not_reuse_version(self):
        first = rule(); first.pop("version")
        saved = save_rule(first, "owner", self.root)
        selection = resolve_selection({"rules": [saved]}, self.root)
        save_rule({**saved, "params": {"limit": 20}}, "owner", self.root)
        self.assertEqual(selection["rules"][0]["params"]["limit"], 3)
        self.assertEqual(resolve_selection({"rules": [{"id": "position", "version": 1}]}, self.root), selection)
        delete_rule("position", 2, self.root)
        saved = save_rule(first, "owner", self.root)
        self.assertEqual(saved["version"], 3)
        self.assertEqual(resolve_selection({"rules": [{"id": "position", "version": 1}]}, self.root), selection)

    def test_aliases_and_all_cta_open_entries_are_gated(self):
        for method in ("stra_enter_long", "enter_long", "stra_enter_short", "enter_short", "stra_set_position", "set_position"):
            with self.subTest(method=method):
                ctx = Context()
                wrapped = self.wrap([rule()])
                wrapped.on_tick(ctx, method, "SHFE.rb.2701", 4)
                self.assertEqual(ctx.calls, [])

    def test_reductions_pass_and_reverse_keeps_conditional_reducing_leg(self):
        ctx = Context(5)
        wrapped = self.wrap([rule(limit=1)])
        wrapped.on_tick(ctx, "set_position", "SHFE.rb.2701", 3)
        self.assertEqual(ctx.position, 3)
        wrapped.on_tick(ctx, "enter_short", "SHFE.rb.2701", 4, limitprice=3300)
        self.assertEqual(ctx.calls[-1], ("set", 0, 3300, 0))
        ctx = Context(-5)
        wrapped = self.wrap([rule(limit=1)])
        wrapped.on_tick(ctx, "stra_exit_short", "SHFE.rb.2701", 5)
        self.assertEqual(ctx.position, 0)

    def test_hft_pending_buy_and_sell_do_not_net_away_exposure(self):
        ctx = Context()
        wrapped = self.wrap([rule(limit=4)], "HFT")
        wrapped.on_tick(ctx, "stra_buy", "SHFE.rb.2701", 3300, 3)
        wrapped.on_tick(ctx, "stra_sell", "SHFE.rb.2701", 3300, 3)
        self.assertEqual(wrapped.on_tick(ctx, "stra_buy", "SHFE.rb.2701", 3300, 2), [])
        self.assertEqual(len(ctx.calls), 2)
        wrapped.on_order(ctx, 1, "SHFE.rb.2701", True, 3, 3, 3300, True, "")
        self.assertEqual(wrapped.on_tick(ctx, "stra_buy", "SHFE.rb.2701", 3300, 2), [3])

    def test_hft_failed_entrust_releases_reservation(self):
        ctx = Context()
        wrapped = self.wrap([rule(limit=3)], "HFT")
        wrapped.on_tick(ctx, "stra_buy", "SHFE.rb.2701", 3300, 3)
        wrapped.on_entrust(ctx, 1, "SHFE.rb.2701", False, "rejected", "")
        self.assertEqual(wrapped.on_tick(ctx, "stra_buy", "SHFE.rb.2701", 3300, 3), [2])

    def test_missing_cash_blocks_opening_but_allows_hft_reduction(self):
        self.account = {"valid": False}
        wrapped = self.wrap([rule("min_available", 1000)], "HFT")
        ctx = Context(-2)
        self.assertEqual(wrapped.on_tick(ctx, "stra_buy", "SHFE.rb.2701", 3300, 5), [1])
        self.assertEqual(ctx.calls, [("buy", 2)])

    def test_daily_loss_uses_previous_balance_and_resets_by_trading_day(self):
        wrapped = self.wrap([rule("day_loss_pct", 2)])
        ctx = Context()
        self.account["equity"] = 97000
        wrapped.on_tick(ctx, "set_position", "SHFE.rb.2701", 1)
        self.assertEqual(ctx.calls, [])
        self.account.update(trading_date=20261021, prebalance=97000)
        wrapped.on_tick(ctx, "set_position", "SHFE.rb.2701", 1)
        self.assertEqual(ctx.position, 1)

    def test_drawdown_and_alert_only_share_current_native_values(self):
        wrapped = self.wrap([rule("drawdown_pct", 5)])
        ctx = Context()
        wrapped.on_tick(ctx, "set_position", "SHFE.rb.2701", 0)
        self.account["equity"] = 94000
        wrapped.on_tick(ctx, "set_position", "SHFE.rb.2701", 1)
        self.assertEqual(ctx.position, 0)
        wrapped = self.wrap([rule("max_order_lots", 1, "alert_only")])
        wrapped.on_tick(ctx, "set_position", "SHFE.rb.2701", 10)
        self.assertEqual(ctx.position, 10)
        self.assertEqual(read_risk_events(self.root/"events.jsonl")[-1]["action"], "alert_only")

    def test_fast_does_not_invent_margin_or_hft_cash_ledger(self):
        for kind, selected in (("cta", rule("min_available", 100)), ("hft", rule("day_loss_pct", 2))):
            with self.assertRaises(ValueError):
                validate_fast_selection({"rules": [selected]}, kind)
        validate_fast_selection({"rules": [rule("drawdown_pct", 5)]}, "cta")

    def test_live_fund_refresh_is_a_request_not_a_valid_receipt(self):
        queried = []
        provider = live_account_provider(self.root, queried.append)
        self.assertFalse(provider(Context(), "sim")["valid"])
        self.assertEqual(queried, ["sim"])
        provider(Context(), "sim")
        self.assertEqual(queried, ["sim"])
        path = self.root/"generated/traders/sim/rtdata.json"
        path.parent.mkdir(parents=True)
        value = dict(balance=100000, dynprofit=-3000, available=50000, updated_at=time.time()*1000, trading_date=20261020)
        path.write_text(json.dumps({"funds": {"CNY": value}}))
        path.with_name(path.name+".status.json").write_text(json.dumps(dict(ready=True, connected=True, trading_date=20261020)))
        snapshot = provider(Context(), "sim")
        self.assertTrue(snapshot["valid"])
        self.assertEqual(snapshot["equity"], 97000)
        value["updated_at"] -= 301000
        path.write_text(json.dumps({"funds": {"CNY": value}}))
        self.assertFalse(provider(Context(), "sim")["valid"])


NATIVE = os.environ.get("WT_PLUS_NATIVE_DIR", "")


@unittest.skipUnless(NATIVE and (Path(NATIVE)/"libWtPorter.so").exists(), "Native Plus library required")
class NativeRiskTests(unittest.TestCase):
    def setUp(self):
        from test_plus_execution import NativeExecutionTests
        self.fixture = NativeExecutionTests("test_fees_slippage_and_insufficient_margin")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_risk(self, source, kind, rules, capital=100000):
        from test_plus_execution import FEES
        root = self.fixture.workspace/"risk-library"
        selection = {"rules": []}
        for item in rules:
            value = {**item, "version": 0}
            selection["rules"].append(save_rule(value, "test", root))
        with patch.dict(os.environ, WT_RISK_LIBRARY=str(root)):
            task = self.fixture.manager.create("test-user", "test-strategy", source, kind, "SHFE.rb.2701",
                "rb2701-synthetic-path", capital=capital, finance=FEES, risk=selection)
        deadline = time.monotonic()+35
        while time.monotonic() < deadline:
            detail = self.fixture.manager.detail("test-user", task["id"])
            if detail["task"]["status"] in ("completed", "failed", "stopped") and "exitcode" in detail["task"]:
                self.assertEqual(detail["task"]["status"], "completed", detail["log_tail"])
                return detail
            time.sleep(.03)
        self.fail("Isolated native replay did not exit")

    def test_hft_and_cta_opening_limits_intercept_before_native_orders(self):
        from test_plus_execution import HFT, CTA
        for kind, source in (("CTA", CTA), ("HFT", HFT)):
            with self.subTest(kind=kind):
                detail = self.run_risk(source, kind, [rule("max_order_lots", 1, identifier=kind.lower())])
                self.assertEqual(detail["orders"], [])
                self.assertEqual(detail["trades"], [])
                self.assertTrue(detail["risk"]["events"])
                self.assertEqual(detail["risk"]["events"][0]["action"], "blocked_opening")

    def test_alert_only_keeps_native_fees_and_margin_rejection(self):
        from test_plus_execution import HFT
        detail = self.run_risk(HFT, "HFT", [rule("max_order_lots", 1, "alert_only", "observe")])
        self.assertEqual(detail["summary"]["commission"], 21)
        self.assertEqual(detail["summary"]["margin"], 0)
        self.assertTrue(detail["risk"]["events"])
        detail = self.run_risk(HFT, "HFT", [], capital=100)
        self.assertEqual(detail["summary"]["rejected_insufficient_funds"], 1)
        self.assertEqual(detail["trades"], [])

    def test_native_available_funds_rule_uses_mocker_account(self):
        from test_plus_execution import HFT
        detail = self.run_risk(HFT, "HFT", [rule("min_available", 200000)])
        self.assertEqual(detail["orders"], [])
        self.assertEqual(detail["risk"]["events"][0]["account_source"], "TraderMocker原生账户")
        detail = self.run_risk(HFT, "HFT", [rule("min_available", 1000, identifier="cash")])
        self.assertEqual(detail["summary"]["commission"], 21)
        self.assertEqual(sum(row["volume"] for row in detail["trades"]), 6)

    def test_hft_at_limit_still_closes_and_uses_native_fee_ledger(self):
        from test_plus_execution import HFT
        detail = self.run_risk(HFT, "HFT", [rule("max_position_lots", 3)])
        self.assertEqual(detail["summary"]["commission"], 21)
        self.assertEqual(sum(row["volume"] for row in detail["trades"]), 6)
        self.assertEqual(detail["risk"]["events"], [])

    def test_fast_cta_native_target_gate_rejects_then_allows_order(self):
        # Reuse the synthetic Tick builder and metadata, in a separate process
        # because WT's engine factories are singletons.
        script = self.fixture.workspace/"fast-risk.py"
        script.write_text('''import json,os
from pathlib import Path
from wtpy import WtBtEngine,EngineType,BaseCtaStrategy
from wtpy.WtCoreDefs import WTSTickStruct
from wtpy.wrapper.WtDtHelper import WtDataHelper
from wtpy.monitor.PlusReplayRunner import load_ticks
from wtpy.monitor.RiskRuntime import attach_risk,fast_account_provider
root=Path.cwd()
ticks=load_ticks({'kind':'synthetic'})
storage=root/'market'
path=storage/'his/ticks/SHFE/20261012/rb2701.dsb'
path.parent.mkdir(parents=True)
assert WtDataHelper().store_ticks(str(path),(WTSTickStruct*len(ticks))(*ticks),len(ticks))
base=root/'validation/replay/common'
files={key:str(base/name) for key,name in [('commodity','commodities.json'),('contract','contracts.json'),('session','sessions.json'),('holiday','holidays.json'),('hot','hots.json')]}
config={'env':{'mocker':'cta','fees':str(base/'fees.json')},'replayer':{'basefiles':files,'mode':'bin','store':{'path':str(storage)},'tick':True,'stime':202610120900,'etime':202610120905},'notifier':{'active':False}}
(root/'config.json').write_text(json.dumps(config))
(root/'log.json').write_text(json.dumps({'root':{'level':'warn','async':False,'sinks':[{'type':'basic_file_sink','filename':str(root/'fast-native.log')} ]}}))
class Strategy(BaseCtaStrategy):
 def __init__(self,name):super().__init__(name);self.step=0
 def on_init(self,ctx):ctx.stra_sub_ticks('SHFE.rb.2701')
 def on_tick(self,ctx,code,tick):
  if self.step==0:ctx.enter_long(code,5)
  elif self.step==1:ctx.enter_long(code,1)
  self.step+=1
 def on_backtest_end(self,ctx):(root/'proof.json').write_text(json.dumps({'position':ctx.stra_get_position('SHFE.rb.2701')}))
engine=WtBtEngine(EngineType.ET_CTA,str(root/'log.json'))
try:
 engine.init('',str(root/'config.json'))
 engine.commitBTConfig()
 attach_risk(engine,{'rules':[{'id':'limit','name':'limit','type':'max_order_lots','mode':'pretrade','params':{'limit':2},'version':1}]},output=root/'fast-risk-events.jsonl',account_provider=fast_account_provider(100000))
 engine.set_cta_strategy(Strategy('fast-risk'))
 engine.run_backtest()
finally:engine.release_backtest()
''', encoding="utf-8")
        process = subprocess.run([sys.executable, str(script)], cwd=self.fixture.workspace,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                 "WTPY_BT_PORTER_LIB": str(Path(NATIVE)/"libWtBtPorter.so")},
            capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout[-2000:]+process.stderr[-2000:])
        self.assertEqual(json.loads((self.fixture.workspace/"proof.json").read_text())["position"], 1)
        events = read_risk_events(self.fixture.workspace/"fast-risk-events.jsonl")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["quantity"], 5)


if __name__ == "__main__":
    unittest.main()
