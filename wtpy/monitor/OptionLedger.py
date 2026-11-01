"""Reconcile one SimNow buyer cycle against queried orders, trades and account balances."""

from decimal import Decimal


def reconcile(before, opened, final, orders, trades, multiplier):
    def cash(v):
        return Decimal(str(v)).quantize(Decimal(".01"))

    # The same callback may be replayed after reconnect.
    unique = {(t["ExchangeID"], t["TradeID"], t["Direction"]): t for t in trades}
    fills = list(unique.values())
    amounts = [
        sum(
            (cash(t["Price"]) * t["Volume"] * multiplier for t in fills if t["Direction"] == side), Decimal(0)
        )
        * sign
        for side, sign in (("0", -1), ("1", 1))
    ]
    stages = (before, opened, final)

    def changes(key):
        return [cash(b["account"][key]) - cash(a["account"][key]) for a, b in zip(stages, stages[1:])]

    fees = changes("Commission")
    net = [a - f for a, f in zip(amounts, fees)]
    positions = [sum(r["Position"] for r in stage["positions"]) for stage in stages]

    def match_key(r, offset):
        return (r["OrderSysID"].strip(), r["InstrumentID"], r["Direction"], r[offset])

    order_keys = {match_key(r, "CombOffsetFlag") for r in orders if r["OrderStatus"] == "0"}
    matched = (
        sorted((t["Direction"], t["OffsetFlag"], t["Volume"]) for t in fills)
        == [("0", "0", 1), ("1", "1", 1)]
        and sorted((o["OrderStatus"], o["VolumeTraded"], o["VolumeTotalOriginal"]) for o in orders)
        == [("0", 1, 1), ("0", 1, 1), ("5", 0, 1)]
        and order_keys == {match_key(t, "OffsetFlag") for t in fills}
        and all(o["OrderSubmitStatus"] != "4" for o in orders)
        and amounts == changes("CashIn")
        and net == changes("Available")
        and net == changes("Balance")
        and positions == [0, 1, 0]
        and not any(s["active"] for s in stages)
    )
    return dict(
        matched=matched,
        positions=positions,
        active_final=len(final["active"]),
        premiums=list(map(str, amounts)),
        fees=list(map(str, fees)),
        net=str(sum(net)),
    )
