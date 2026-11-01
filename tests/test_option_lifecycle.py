from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from wtpy.monitor.OptionFrames import guard_option_orders, publish_frame, read_option_frame
from wtpy.monitor.OptionLifecycle import Lifecycle, covered_spread, delivery, expiry_loss, reserve, td
from wtpy.monitor.OptionLedger import reconcile
from wtpy.monitor.OptionModels import contract_metrics, qdate


class OptionLifecycleTests(unittest.TestCase):
    def test_independent_vol_and_expiring_leg(self):
        m = contract_metrics(100, 100, 5, 30, 0.02, True, "american", qdate(20261030), 0.5)
        self.assertAlmostEqual(m["theoretical"], 5, 6)
        self.assertGreater(m["scenario_price"], m["theoretical"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = "c" * 24
            folder = root / "analysis" / session
            folder.mkdir(parents=True)

            def write(end):
                publish_frame(
                    folder,
                    dict(
                        published=time.time(),
                        mode="simulation",
                        rows=[
                            dict(symbol="A", iv=0.3, valid_until=time.time() + 10),
                            dict(symbol="B", iv=0.3, valid_until=end),
                        ],
                    ),
                )

            def valid():
                read_option_frame(root, session, mode="simulation", symbols=["A", "B"])

            sent = []
            ctx = SimpleNamespace(
                stra_buy=lambda code: sent.append(code),
                stra_sell=lambda code: sent.append(code),
                stra_log_text=lambda msg: None,
            )
            guard_option_orders(ctx, "HFT", valid, {"A", "B"})
            write(time.time() - 1)
            ctx.stra_buy("A")
            write(time.time() + 10)
            ctx.stra_buy("A")
            ctx.stra_sell("C")
            self.assertEqual(sent, ["A"])
            (folder / "frame").unlink()
            ctx.stra_buy("A")
            self.assertEqual(sent, ["A"])

    def test_partial_ioc_never_creates_an_unprotected_short(self):
        calls = []

        def leg(name, result):
            def run(q):
                calls.append((name, q))
                return min(result, q)

            return run

        self.assertEqual(
            covered_spread(4, leg("buy", 3), leg("sell", 1), leg("reduce", 1)),
            dict(paired=1, residual_long=1),
        )
        self.assertEqual(calls, [("buy", 4), ("sell", 3), ("reduce", 2)])

        def unknown(q):
            raise TimeoutError("unknown")

        with self.assertRaises(TimeoutError):
            covered_spread(2, leg("buy", 2), unknown, lambda _: self.fail("unknown sell fill"))
        self.assertEqual(reserve(dict(FixedMargin=400, MiniMargin=500, Royalty=50), 2), 1100)
        long = dict(StrikePrice=110, VolumeMultiple=10, OptionsType="1")
        self.assertEqual(expiry_loss(long, dict(StrikePrice=100), 3, 7, 2), 120)

    def test_exercise_requests_use_long_holdings_and_do_not_imply_delivery(self):
        client = Mock()
        client.login = dict(MaxOrderRef="7", TradingDay="20261030")
        client.who = dict(BrokerID="9999", InvestorID="synthetic")
        svc = Lifecycle(client, Path("."), Mock())
        c = dict(
            InstrumentID="m0001-C-100",
            UnderlyingInstrID="m0001",
            OptionsType="1",
            ExpireDate="20261102",
            StrikePrice=100,
            VolumeMultiple=10,
        )
        svc.positions = Mock(return_value=dict(long=1, frozen_long=0))
        svc.available = lambda: 10000
        svc.query = Mock(
            return_value=[dict(IsRelative=0, LongMarginRatioByMoney=0.1, LongMarginRatioByVolume=0)]
        )
        svc.submit = Mock()
        svc.wait = Mock(return_value={"ExecResult": td.THOST_FTDC_OER_NoExec})
        with patch.object(svc, "future_ask", return_value=102):
            with self.assertRaises(ValueError):
                svc.declaration(c, 2, False, True, 200)
            with self.assertRaises(ValueError):
                svc.declaration(c, 1, False, True, 100)
            svc.submit.assert_not_called()
            r = svc.declaration(c, 1, False, True, 200)
            self.assertEqual(r["ExecResult"], td.THOST_FTDC_OER_NoExec)
            action = svc.submit.call_args.args[1]
            self.assertEqual(
                (action.InstrumentID, action.ActionType, action.Volume),
                (c["InstrumentID"], td.THOST_FTDC_ACTP_Exec, 1),
            )
        for holder, call, change in [
            ("long", True, 1),
            ("short", True, -1),
            ("long", False, -1),
            ("short", False, 1),
        ]:
            d = delivery({holder: 1, "future_net": 0}, {holder: 0, "future_net": change}, call, holder, 1)
            self.assertEqual(d["actual_future"], d["expected_future"])
            self.assertEqual(d["actual_option"], d["expected_option"])

    def test_ledger_rejects_duplicate_or_missing_economic_legs(self):
        def snap(n, cash, fee):
            return dict(
                positions=[{"Position": n}] if n else [],
                active=[],
                account=dict(
                    CashIn=cash, Commission=fee, Available=1000 + cash - fee, Balance=1000 + cash - fee
                ),
            )

        a, b, c = snap(0, 0, 0), snap(1, -100, 1), snap(0, 20, 2)
        orders = [
            dict(
                OrderSysID=str(i),
                InstrumentID="EXAMPLE",
                Direction="1" if i == 3 else "0",
                CombOffsetFlag="1" if i == 3 else "0",
                OrderSubmitStatus="3",
                OrderStatus=status,
                VolumeTraded=n,
                VolumeTotalOriginal=1,
            )
            for i, status, n in ((1, "5", 0), (2, "0", 1), (3, "0", 1))
        ]
        fills = [
            dict(
                ExchangeID="DCE",
                InstrumentID="EXAMPLE",
                TradeID=str(i),
                OrderSysID=str(i),
                Direction=side,
                OffsetFlag=offset,
                Price=p,
                Volume=1,
            )
            for i, side, offset, p in ((2, "0", "0", 10), (3, "1", "1", 12))
        ]
        self.assertTrue(reconcile(a, b, c, orders, fills + [fills[1]], 10)["matched"])
        c["account"]["Available"] += 1
        self.assertFalse(reconcile(a, b, c, orders, fills, 10)["matched"])
