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
