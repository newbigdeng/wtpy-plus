from pathlib import Path
import json

import pytest
import yaml

from wtpy.monitor.DeferredSubscriptions import apply_at_start, set_plan, state


def test_deferred_plan_checks_revision_and_keeps_newer_native_settings(tmp_path):
    pytest.importorskip("fcntl")
    hub = tmp_path/"datahub"
    hub.mkdir()
    codes = ["X.a1", "X.a2", "X.a3"]
    directory = tmp_path/"pending"
    directory.mkdir()
    (hub/"dtcfg.ctp.yaml").write_text("parsers: md.yaml\nbasefiles: {contract: c.json, commodity: p.json}")
    (hub/"p.json").write_text(json.dumps({"X":{"a":{}}}))
    (hub/"c.json").write_text(json.dumps({"X":{c[2:]:{"product":"a"} for c in codes}}))
    path = hub/"md.yaml"
    def parser(code):
        path.write_text(yaml.safe_dump({"parsers":[dict(active=True,module="ParserCTP",code=code)]}))
    def selected():
        return yaml.safe_load(path.read_text())["parsers"][0]["code"]
    snapshot = {"catalog":[{"subscription_code":c} for c in codes], "selected":codes[:1]}
    parser(codes[0])
    set_plan(directory,snapshot,"save",codes[1:2],0)
    assert selected() == codes[0]
    with pytest.raises(ValueError):
        set_plan(directory,snapshot,"cancel",[],0)
    apply_at_start(directory,hub)
    assert selected() == codes[1] and state(directory)["symbols"] is None
    snapshot["selected"] = codes[1:2]
    set_plan(directory,snapshot,"save",codes[2:],2)
    parser(codes[0])
    apply_at_start(directory,hub)
    assert selected() == codes[0] and "已变更" in state(directory)["message"]
    parser("")
    snapshot["selected"] = codes
    set_plan(directory,snapshot,"save",codes,4)
    apply_at_start(directory,hub)
    assert selected() == ",".join(codes)
