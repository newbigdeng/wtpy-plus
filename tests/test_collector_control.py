import json
from pathlib import Path
import time

import pytest

from wtpy.monitor import CollectorControl as control
from wtpy.monitor.CommodityOptionSim import simnow_profile
from wtpy.monitor.OptionChain import subscription_chains


def test_saved_symbols_never_start_recording_and_stale_edit_is_rejected(tmp_path):
    pytest.importorskip("fcntl")
    saved = control.change(tmp_path, "crypto", ["BTC-USD"], "save", 0)
    assert saved["state"] == "offline" and not saved["enabled"]
    with pytest.raises(ValueError):
        control.change(tmp_path, "crypto", ["ETH-USD"], "start", 0)
    with pytest.raises(ValueError):
        control.change(tmp_path, "crypto", ["BTC-USD"], "start", 1)
    hub = tmp_path/"datahub"
    control.atomic_json(hub/"status.json", dict(heartbeat=time.time(), state="stopped", revision=1))
    started = control.change(tmp_path, "crypto", saved["symbols"], "start", 1)
    assert started["enabled"] and started["state"] == "pending"
    stopped = control.change(tmp_path, "crypto", [], "stop", 2)
    assert not stopped["enabled"] and stopped["symbols"] == ["BTC-USD"]


def test_option_shortcuts_respect_subscription_and_month(tmp_path):
    ref = tmp_path/"reference"
    ref.mkdir()
    chains = {k:[{"expiry":20990101}] for k in ("DCE.m2701","DCE.m2703","DCE.c2703","DCE.cs2701")}
    (ref/"chain.json").write_text(json.dumps({"chains":chains}))
    rows = [dict(exchange="DCE", ticker=t, product=t[:-4], product_name=t[:-4],
                 subscription_code="DCE."+t) for t in ("m2703","c2701","cs2701")]
    result = subscription_chains(tmp_path, dict(catalog=rows, selected=["DCE.m2703","DCE.c2701"]))
    assert [(r["future"],r["underlying"]) for r in result] == [("DCE.m2703","DCE.m2703"),("DCE.c2701","DCE.c2703")]


def test_counter_file_only_accepts_simnow(tmp_path):
    path = tmp_path/"traders.json"
    row = dict(module="TraderCTP",active=True,broker="9999",user="example",appid="test",authcode="test",
               front="tcp://182.254.243.31:40001",**{"pass":"test"})
    path.write_text(json.dumps({"traders":[row]}))
    assert simnow_profile(path)["front"] == row["front"]
    row["front"] = "tcp://broker.example:40001"
    path.write_text(json.dumps({"traders":[row]}))
    with pytest.raises(ValueError):
        simnow_profile(path)
