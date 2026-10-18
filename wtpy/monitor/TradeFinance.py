"""WT fee and margin configuration shared by replay and read-only reporting."""
import json
import math
from pathlib import Path


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def read_table(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def fee_rule(table, code, source):
    parts = str(code).split(".")
    keys = [parts[0]+"."+parts[1]+parts[2], ".".join(parts[:2])] if len(parts) == 3 else [str(code)]
    rule = next(((key, table[key]) for key in keys if key in table), (None, None))
    key, original = rule
    values = {field: number(original.get(field)) if isinstance(original, dict) else None
              for field in ("open", "close", "closetoday")}
    byvolume = original.get("byvolume") if isinstance(original, dict) else None
    minimum = number(original.get("minfee", 0)) if isinstance(original, dict) else None
    known = isinstance(byvolume, bool) and minimum is not None and minimum >= 0 and all(v is not None and v >= 0 for v in values.values())
    return {"known": known, "source": source, "key": key,
            "byvolume": byvolume if known else None,
            **(values if known else {field: None for field in values}),
            "minfee": minimum if known else None}


def contract_margin(contracts, code, commodities=None):
    parts = str(code).split(".")
    item = contracts.get(parts[0], {}).get(parts[1]+parts[2], {}) if len(parts) == 3 else {}
    long = number(item.get("longmarginratio"))
    short = number(item.get("shortmarginratio"))
    commodity = (commodities or {}).get(parts[0], {}).get(parts[1], {}) if len(parts) == 3 else {}
    commodity_rate = number(commodity.get("marginrate"))
    source = "WT contracts.json"
    if commodity_rate is not None and commodity_rate != 0:
        long = short = commodity_rate
        source = "WT commodities.json marginrate"
    long = long if long is not None and 0 < long <= 1 else None
    short = short if short is not None and 0 < short <= 1 else None
    return {"known": long is not None and short is not None,
            "source": source, "long": long, "short": short}


def dataset_finance(item):
    base = Path(item["basefiles"])
    return {"fees": fee_rule(read_table(base/"fees.json"), item["code"], "WT fees.json"),
            "margin": contract_margin(read_table(base/"contracts.json"), item["code"], read_table(base/"commodities.json")),
            "slippage_ticks": 0}


def effective_finance(item, overrides=None):
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict) or set(overrides) - {"fees", "marginrate", "slippage_ticks"}:
        raise ValueError("费用参数只支持fees、marginrate、slippage_ticks")
    result = dataset_finance(item)
    if "fees" in overrides:
        fees = overrides["fees"]
        if not isinstance(fees, dict) or set(fees) - {"byvolume", "open", "close", "closetoday", "minfee"}:
            raise ValueError("手续费使用WT字段byvolume/open/close/closetoday/minfee")
        result["fees"] = fee_rule({".".join(item["code"].split(".")[:2]): fees}, item["code"], "用户配置")
    if not result["fees"]["known"]:
        raise ValueError("数据集缺少有效手续费，请填写开仓、平仓、平今费用及计费方式；零费率也须明确填写")
    marginrate = overrides.get("marginrate")
    if marginrate is not None:
        marginrate = number(marginrate)
        if marginrate is None or not 0 < marginrate <= 1:
            raise ValueError("保证金比例必须大于0且不超过1")
        result["margin"] = {"known": True, "source": "用户配置", "long": marginrate, "short": marginrate}
    if not result["margin"]["known"]:
        raise ValueError("数据集缺少多空保证金比例，请显式填写保证金比例")
    slippage = overrides.get("slippage_ticks", 0)
    if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 10000:
        raise ValueError("滑点必须为0至10000的整数跳数")
    result.update(version=1, marginrate=marginrate, slippage_ticks=slippage,
                  fee_minimum_scope="每次成交", account_source="TraderMocker原生账户")
    return result
