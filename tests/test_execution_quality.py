import tempfile
import importlib.util
import importlib
import sys
from pathlib import Path
import unittest

# Load the pure reader package without importing trading/native runtime libraries.
package = importlib.util.module_from_spec(importlib.util.spec_from_loader("execution_readers", loader=None, is_package=True))
package.__path__ = [str(Path(__file__).parents[1] / "wtpy/monitor")]
sys.modules[package.__name__] = package
module = importlib.import_module("execution_readers.ExecutionQuality")
execution_quality, summarize_execution = module.execution_quality, module.summarize_execution
event_module = importlib.import_module("execution_readers.ExecutionQualityEvents")
summarize_events = event_module.summarize_events
NativeLedgerCache = importlib.import_module("execution_readers.ExecutionQualityFiles").NativeLedgerCache


NOW = 1792980000000


def order(**changes):
    return {"channel": "sim", "localid": "10", "date": "20261026", "inserttime": str(NOW),
            "code": "SHFE.rb.2701", "action": "OL", "volume": "3", "traded": "3",
            "price": "3000", "orderid": "  42", "canceled": "FALSE", **changes}


def trade(**changes):
    return {"channel": "sim", "localid": "10", "date": "20261026", "time": str(NOW),
            "code": "SHFE.rb.2701", "action": "OL", "volume": "3", "price": "2999",
            "orderid": "42", "tradeid": "7", **changes}


class ExecutionQualityTests(unittest.TestCase):
    def test_partial_cancellation_and_volume_denominator(self):
        result = summarize_execution([order(traded="1", canceled="TRUE"),
                                      order(localid="11", orderid="43", volume="1", traded="0", canceled="TRUE")],
                                     [trade(volume="1")])
        self.assertEqual(result["summary"]["terminal_volume_fill_rate"], .25)
        self.assertEqual(result["summary"]["terminal_full_order_rate"], 0)
        self.assertEqual(result["summary"]["canceled"], 2)
        self.assertIsNone(result["summary"]["all_submission_fill_rate"])

    def test_each_fill_vwap_sign_and_seconds_are_not_local_latency(self):
        result = summarize_execution([order()], [trade(volume="1"), trade(tradeid="8", volume="2", price="2998", time=str(NOW+2000))],
                                     contracts=[{"code": "SHFE.rb.2701", "price_tick": 1}])
        row = result["orders"][0]
        self.assertAlmostEqual(row["vwap"], 2998 + 1/3)
        self.assertAlmostEqual(row["limit_deviation_ticks"], -5/3)
        self.assertEqual(row["exchange_first_fill_ms"], 0)
        self.assertTrue(row["first_fill_same_second"])
        self.assertEqual(row["exchange_last_fill_ms"], 2000)
        self.assertIsNone(row["arrival_slippage"])
        self.assertIsNone(row["submit_to_ack_ms"])
        self.assertEqual(len(result["trades"]), 2)

    def test_close_short_is_buy_and_close_long_is_sell(self):
        for action, expected in (("CNS", -1), ("CS", -1), ("CNL", 1), ("CL", 1), ("OS", 1)):
            row = summarize_execution([order(action=action)], [trade(action=action)])["orders"][0]
            self.assertEqual(row["limit_deviation"], expected)

    def test_same_ids_in_other_channel_and_contract_do_not_join(self):
        result = summarize_execution([order()], [trade(channel="other"), trade(code="DCE.i.2701")])
        self.assertEqual(result["summary"]["unmatched_fills"], 2)
        self.assertFalse(result["orders"][0]["reconciled"])

    def test_repeated_terminal_notifications_and_trade_callbacks(self):
        result = summarize_execution([order(), order()], [trade(), trade()])
        self.assertEqual(result["summary"]["terminal_orders"], 1)
        self.assertEqual(result["summary"]["terminal_filled_volume"], 3)
        self.assertEqual(result["summary"]["duplicate_orders"], 1)
        self.assertEqual(result["summary"]["duplicate_fills"], 1)

    def test_midnight_order_and_reused_ids_join_by_insert_clock(self):
        result = summarize_execution([order(date="20261025"), order(date="20261026", inserttime=str(NOW+86400000))],
                                     [trade(time=str(NOW+1000))])
        self.assertEqual(result["summary"]["unmatched_fills"], 0)
        self.assertTrue(result["orders"][1]["reconciled"])
        self.assertFalse(result["orders"][0]["reconciled"])

    def test_missing_or_reversed_clock_does_not_become_zero(self):
        for value in ("", "0", str(NOW-1000)):
            row = summarize_execution([order()], [trade(time=value)])["orders"][0]
            self.assertIsNone(row["exchange_first_fill_ms"])

    def test_no_limit_and_unknown_terminal_status_are_not_slippage_or_rejection(self):
        result = summarize_execution([order(price="0", traded="1")], [trade(volume="1")])
        self.assertIsNone(result["orders"][0]["limit_deviation"])
        self.assertEqual(result["orders"][0]["status"], "terminal_unclassified")
        self.assertIsNone(result["summary"]["rejected_orders"])

    def test_invalid_numbers_are_visible_and_empty_rates_are_null(self):
        result = summarize_execution([order(volume="nan"), order(traded="4")], [trade(volume="-1")])
        self.assertEqual(result["summary"]["invalid_rows"], 3)
        self.assertIsNone(result["summary"]["terminal_volume_fill_rate"])

    def test_file_replacement_and_missing_file_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generated/traders/sim"
            path.mkdir(parents=True)
            group = {"path": directory}
            empty = execution_quality(group, ["sim"])
            self.assertFalse(empty["sources"][0]["orders_available"])
            headers = "localid,date,inserttime,code,action,volume,traded,price,orderid,canceled,remark\n"
            ledger = path / "orders.csv"
            ledger.write_text(headers+f"10,20261026,{NOW},SHFE.rb.2701,OL,3,3,3000,42,FALSE,filled\n")
            result = execution_quality(group, ["sim"])
            self.assertEqual(result["summary"]["terminal_orders"], 1)
            ledger.write_text(headers)
            self.assertEqual(execution_quality(group, ["sim"])["summary"]["terminal_orders"], 0)
            self.assertEqual(ledger.read_text(), headers)

    def test_detail_limit_does_not_change_summary(self):
        result = summarize_execution([order(), order(localid="11")], [], limit=1)
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(result["summary"]["terminal_orders"], 2)


def event(kind, **changes):
    return {"schema":1,"event":kind,"channel":"sim","trading_date":20261026,
            "clock_id":"boot-a","localid":10,"entrust_id":"session.1","code":"rb2701",
            "side":"buy","qty":3,"price":3000,"wall_time_ns":1792980000000000000,
            "mono_time_ns":1000000000,**changes}


class ExecutionEventTests(unittest.TestCase):
    def test_real_submit_quote_and_monotonic_ack_partial_fill(self):
        result = summarize_events([
            event("submission",quote={"bid":2999,"ask":3000,"last":2999,"updated_at":NOW},benchmark_kind="opposite_touch"),
            event("ack",orderid="42",mono_time_ns=1001500000),
            event("order_update",orderid="42",filled_qty=1,left_qty=2,mono_time_ns=1002000000),
            event("fill",orderid="42",tradeid="7",qty=1,price=3001,mono_time_ns=1002500000)])
        row = result["orders"][0]
        self.assertEqual(row["arrival_slippage"],1)
        self.assertEqual(row["submit_to_ack_ms"],1.5)
        self.assertEqual(row["submit_to_first_fill_ms"],2.5)
        self.assertIsNone(row["submit_to_last_fill_ms"])
        self.assertEqual(row["status"],"open")
        self.assertEqual(result["summary"]["volume_fill_rate"],1/3)

    def test_reject_cancel_and_open_orders_all_enter_submission_denominator(self):
        events=[event("submission"),event("reject"),
                event("submission",localid=11,entrust_id="session.2"),
                event("cancel_request",localid=11,entrust_id="session.2",mono_time_ns=1001000000),
                event("cancel_ack",localid=11,entrust_id="session.2",mono_time_ns=1003500000),
                event("submission",localid=12,entrust_id="session.3")]
        result=summarize_events(events)
        self.assertEqual(result["summary"]["submitted_orders"],3)
        self.assertEqual(result["summary"]["submitted_volume"],9)
        self.assertEqual(result["summary"]["rejected"],1)
        self.assertEqual(result["summary"]["canceled"],1)
        self.assertEqual(result["summary"]["open"],1)
        self.assertEqual(result["orders"][1]["cancel_to_ack_ms"],2.5)

    def test_cross_process_callback_retains_identity_but_has_no_latency(self):
        result=summarize_events([event("submission"),event("ack",orderid="42"),
                                 event("fill",localid=0,entrust_id="",orderid="42",clock_id="boot-b",tradeid="7",qty=3)])
        self.assertEqual(result["summary"]["unmatched_events"],0)
        self.assertEqual(result["orders"][0]["status"],"filled")
        self.assertIsNone(result["orders"][0]["submit_to_first_fill_ms"])

    def test_same_localid_on_restart_is_not_same_order(self):
        result=summarize_events([event("submission"),event("submission",clock_id="boot-b",entrust_id="session.2"),
                                 event("reject",clock_id="boot-b",entrust_id="session.2")])
        self.assertEqual(result["summary"]["submitted_orders"],2)
        self.assertEqual(result["summary"]["rejected"],1)
        self.assertEqual(result["summary"]["open"],1)

    def test_full_fill_spanning_restart_does_not_report_partial_clock_as_completion(self):
        result=summarize_events([event("submission"),event("ack",orderid="42"),
                                 event("fill",tradeid="7",orderid="42",qty=1,mono_time_ns=1002000000),
                                 event("fill",tradeid="8",orderid="42",qty=2,clock_id="boot-b",localid=0,entrust_id="")])
        self.assertEqual(result["orders"][0]["status"],"filled")
        self.assertIsNone(result["orders"][0]["submit_to_first_fill_ms"])
        self.assertIsNone(result["orders"][0]["submit_to_last_fill_ms"])

    def test_missing_touch_never_uses_limit_as_arrival_price(self):
        result=summarize_events([event("submission",quote={"ask":0},benchmark_kind="opposite_touch"),
                                 event("fill",tradeid="7",qty=3)])
        self.assertIsNone(result["orders"][0]["arrival_slippage"])

    def test_sell_uses_bid_and_duplicate_fills_do_not_inflate_rate(self):
        fill=event("fill",side="sell",tradeid="7",price=2998,qty=3,mono_time_ns=1001000000)
        result=summarize_events([event("submission",side="sell",quote={"bid":2999,"ask":3000},benchmark_kind="opposite_touch"),fill,fill])
        self.assertEqual(result["orders"][0]["arrival_slippage"],1)
        self.assertEqual(result["summary"]["volume_fill_rate"],1)
        self.assertEqual(result["summary"]["duplicate_fills"],1)

    def test_partial_fill_callbacks_not_yet_complete_leave_last_latency_blank(self):
        result=summarize_events([event("submission"),event("order_update",filled_qty=3,left_qty=0),
                                 event("fill",tradeid="7",qty=1,mono_time_ns=1001000000)])
        self.assertEqual(result["orders"][0]["status"],"filled")
        self.assertFalse(result["orders"][0]["reconciled"])
        self.assertIsNone(result["orders"][0]["submit_to_last_fill_ms"])


class NativeLedgerCacheTests(unittest.TestCase):
    def test_append_retains_one_version_and_retries_partial_last_line(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"events.jsonl"
            first=json.dumps(event("submission"))
            second=json.dumps(event("reject"))
            path.write_text(first+'\n'+second[:20])
            cache=NativeLedgerCache()
            rows,available,invalid=cache.read(path,"jsonl")
            self.assertEqual((len(rows),available,invalid),(1,True,0))
            original=next(iter(cache.entries.values()))["rows"]
            with path.open("a") as stream:stream.write(second[20:]+'\n')
            rows,_,invalid=cache.read(path,"jsonl")
            self.assertEqual((len(rows),invalid),(2,0))
            self.assertEqual(len(cache.entries),1)
            self.assertIs(next(iter(cache.entries.values()))["rows"],original)
            path.write_text(first+'\n')
            self.assertEqual(len(cache.read(path,"jsonl")[0]),1)

    def test_invalid_complete_event_and_path_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=NativeLedgerCache(max_paths=1)
            for name in ("first","second"):
                path=Path(directory)/name;path.write_text('{bad}\n{"schema":2}\n')
                self.assertEqual(cache.read(path,"jsonl")[2],2)
            self.assertEqual(len(cache.entries),1)

    def test_incremental_native_csv_has_one_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"orders.csv";path.write_bytes(b"localid,volume\n1,3\n2,")
            cache=NativeLedgerCache()
            self.assertEqual(cache.read(path,"csv")[0],({"localid":"1","volume":"3"},))
            with path.open("ab") as stream:stream.write(b"5\n")
            self.assertEqual(cache.read(path,"csv")[0][-1],{"localid":"2","volume":"5"})
            # A larger in-place replacement must not be mistaken for an append.
            path.write_bytes(b"localid,volume\n99,7\n88,9\n77,5\n")
            rows=cache.read(path,"csv")[0]
            self.assertEqual(len(rows),3)
            self.assertEqual(rows[0],{"localid":"99","volume":"7"})


if __name__ == "__main__":
    unittest.main()
