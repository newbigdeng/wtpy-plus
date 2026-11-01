from wtpy.monitor.RunDiagnostics import option_details, roll_pairs


def test_attribution_uses_old_positions_and_preserves_missing_intervals():
    days = ["20261026", "20261027", "20261028"]

    def marks(prices):
        return {d: dict(price=v, clock=d) for d, v in zip(days, prices)}

    legs = {
        "f": dict(under="f", strike=None, mult=10, marks=marks([3100, 3120, 3110])),
        "c": dict(
            under="f",
            strike=3100,
            expiry=20261207,
            call=True,
            mult=10,
            marks=marks([80, 91, None]),
        ),
    }
    positions = {days[0]: {"c": 1, "f": -1}, days[1]: {"c": 2}, days[2]: {}}
    r = option_details(legs, positions, days, dict(zip(days, [10000, 9908, 9850])))
    first, second = r["attribution"]
    assert first["pnl"] == -92 and first["gamma"] > 0
    assert (
        abs(
            first["pnl"]
            - sum(first[k] for k in ("delta", "gamma", "vega", "theta", "residual"))
        )
        < 1e-9
    )
    assert second["pnl"] == -58 and second["residual"] is None
    assert r["payoffs"][0]["day"] == days[1]


def test_explicit_roll_requires_matched_direction_size_and_product():
    common = dict(tag="roll:next", qty=1, fee=2, direct="SHORT", time="202610281430")
    fills = [
        dict(common, action="CLOSE", code="DCE.m.2701", price=3100),
        dict(common, action="OPEN", code="DCE.m.2705", price=3120),
    ]
    scales = {r["code"]: 10 for r in fills}
    (row,) = roll_pairs(fills, scales)
    assert row["cost"] == -196
    assert not roll_pairs([fills[0], dict(fills[1], tag="")], scales)
