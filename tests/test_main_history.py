"""Trading-day decisions and forward adjustments must preserve the observed prefix."""

from wtpy.monitor.MainHistory import main_plan, ranked_successors
from wtpy.monitor.MainReplay import trade_day


def test_next_session_roll_and_immutable_prefix():
    days = ["20261026", "20261027", "20261028", "20261029", "20261030"]

    def quotes(oi, px):
        return {
            day: dict(oi=pos, volume=20, close=price)
            for day, pos, price in zip(days, oi, px)
        }

    data = {
        "2701": quotes([20, 18, 8, 16, 30], [100, 105, 110, 108, 112]),
        "2705": quotes([9, 12, 22, 9, 1], [140, 144, 150, 148, 149]),
    }
    before = main_plan(data, days[1], days[2])
    plan = main_plan(data, days[1], days[-1])
    assert before == plan[:2]
    assert [r["month"] for r in plan] == ["2701", "2701", "2705", "2705"]
    assert plan[2]["factor"] == 110 / 150
    data["2705"][days[-1]]["close"] = 987
    assert main_plan(data, days[1], days[-1]) == plan
    assert trade_day("202610302115", ["20261030", "20261102"]) == "20261102"


def test_roll_strike_is_relative_to_both_underlying_prices():
    candidates = [dict(raw="new-C-" + str(k), strike=k) for k in (100, 105, 115, 125)]
    assert ranked_successors(105, 100, 110, candidates)[0]["strike"] == 115

def test_absent_weekday_in_previous_year_is_not_a_holiday():
    from datetime import date
    import pytest
    from wtpy.monitor.MarketHistory import open_day

    assert not open_day(date(2025, 10, 8))
    quote = {"oi": 20, "volume": 10, "close": 100}
    with pytest.raises(ValueError, match="2025-10-09"):
        main_plan({"2601": {d: quote for d in ("20250930", "20251010")}}, "20251009", "20251010")

def test_expiry_roll_uses_a_tradable_month_even_when_the_future_has_not_rolled(tmp_path, monkeypatch):
    import json
    from wtpy.monitor import MainReplay

    days = ("20260331", "20260401", "20260402", "20260403")
    def bars(price, oi):
        return {d: dict(open=price, high=price, low=price, close=price, volume=1, oi=oi) for d in days}
    futures = {"2605": bars(2700, 100), "2607": bars(2750, 80), "2609": bars(2800, 65)}
    plan = [dict(prior=a, day=b, month="2605", factor=1) for a, b in zip(days, days[1:])]
    monkeypatch.setattr(MainReplay, "product_history", lambda *_: (futures, plan))
    old, new = "m2605-P-2700", "m2609-P-2800"
    def exact(root, ex, product, day, ids):
        # The intervening future has OI but no qualifying option on the decision day.
        return {raw: dict(underlying="DCE."+raw.split("-")[0], strike=float(raw.split("-")[-1]),
                          opened="20260301", side="看跌", expiry=expiry)
                for raw, expiry in ((old, "20260402"), (new, "20260818")) if raw in ids.split(",")}
    monkeypatch.setattr(MainReplay, "listed_options", exact)
    monkeypatch.setattr(MainReplay, "option_day", lambda root, ex, raw, *_: bars(12 if raw==old else 24, 2))
    common = tmp_path/"common"
    common.mkdir()
    for name, value in (("commodities", {"DCE": {"m": {"session":"DAY"}, "m_o": {}}}),
                        ("sessions", {}), ("holidays", {})):
        (common/(name+".json")).write_text(json.dumps(value))
    folder=tmp_path/"run";folder.mkdir()
    _, rows=MainReplay.prepare(folder,tmp_path,dict(product="DCE.m",option="DCE."+old,asset="option",
                             start="2026-04-01",end="2026-04-03",period="d1",fee=1))
    assert [r["raw"] for r in rows]==[old,new,new]
    assert rows[1]["factor"]==.5 and rows[1]["prior"]=="20260401"
