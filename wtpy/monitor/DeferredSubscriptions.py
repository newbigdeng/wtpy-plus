"""Apply an idle futures hub's saved selection at its next native startup."""
from contextlib import contextmanager
import json
from pathlib import Path

import yaml
from wtpy.StrategyRuntime import atomic_json
from wtpy.monitor.MarketSubscriptions import _configured_contracts, _inside, _load, _write


@contextmanager
def locked(directory):
    import fcntl
    with (directory/"plan.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def state(directory):
    path = directory/"plan.json"
    return json.loads(path.read_bytes()) if path.is_file() else {"revision": 0, "symbols": None}


def set_plan(directory, snapshot, action, symbols, revision):
    with locked(directory):
        current = state(directory)
        if revision != current["revision"]:
            raise ValueError("其他页面或中台已更新订阅，请刷新")
        if action == "save":
            symbols = sorted(set(symbols))
            valid = {r["subscription_code"] for r in snapshot["catalog"]}
            if not symbols or not set(symbols) <= valid:
                raise ValueError("请从该环境的合约表中选择具体合约")
        else:
            symbols = None
        new = {"revision": revision+1, "symbols": symbols, "base": sorted(snapshot["selected"])}
        atomic_json(directory/"plan.json", new)
        return new


def apply_at_start(directory, datahub):
    """Run before WtDtEngine.initialize; never restart a datahub or touch a running parser."""
    directory, datahub = Path(directory), Path(datahub).resolve()
    if not (directory/"plan.json").is_file():
        return
    with locked(directory):
        plan = state(directory)
        if not plan["symbols"]:
            return
        config = _load(datahub/"dtcfg.ctp.yaml")
        root = datahub.parent
        path = _inside(root, datahub, config["parsers"])
        parsers = _load(path)
        feeds = [r for r in parsers["parsers"] if r.get("active") and "parserctp" in r.get("module","").lower()]
        if len(feeds) != 1:
            raise ValueError("待生效订阅需要一个 CTP 行情通道")
        contracts = _load(_inside(root,datahub,config["basefiles"]["contract"]))
        products = _load(_inside(root,datahub,config["basefiles"]["commodity"]))
        catalog = [dict(subscription_code=ex+"."+ticker, exchange=ex, ticker=ticker, product=row["product"])
                   for ex, items in contracts.items() for ticker,row in items.items()
                   if row.get("product") in products.get(ex,{})]
        selected, _ = _configured_contracts(feeds[0], catalog)
        target = plan["symbols"]
        if (sorted(selected) not in (plan["base"], target)
                or not set(target) <= {r["subscription_code"] for r in catalog}):
            message = "原订阅或合约表已变更，继续使用原配置。请重新选择订阅。"
        else:
            explicit = ",".join(target)
            if feeds[0].get("code") != explicit or feeds[0].get("filter"):
                feeds[0].update(code=explicit, filter="")
                _write(path, yaml.safe_dump(parsers, allow_unicode=True, sort_keys=False))
            message = "订阅已生效"
        atomic_json(directory/"plan.json", {"revision":plan["revision"]+1, "symbols":None, "message":message})
