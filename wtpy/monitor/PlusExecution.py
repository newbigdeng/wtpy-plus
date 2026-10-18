"""Server allowlist for the existing WT CTA execution units."""
import math

EXECUTORS = [
    {"id": "minimpact", "label": "MinImpact · 最小冲击", "native": "WtExeFact.WtMinImpactExeUnit"},
    {"id": "twap", "label": "TWAP · 按时间拆单", "native": "WtExeFact.WtTWapExeUnit"},
    {"id": "vwap", "label": "VWAP · 按预设成交量曲线拆单", "native": "WtExeFact.WtVWapExeUnit"},
]


def effective_execution(kind, start, end, supplied=None):
    if kind == "HFT":
        if supplied:
            raise ValueError("HFT由策略直接委托，不使用CTA执行器")
        return None
    supplied = {} if supplied is None else supplied
    if not isinstance(supplied, dict) or set(supplied) - {"id", "params", "volume_weights"}:
        raise ValueError("执行配置仅支持id、params、volume_weights")
    identifier = supplied.get("id", "minimpact")
    item = next((row for row in EXECUTORS if row["id"] == identifier), None)
    if item is None:
        raise ValueError("未知执行策略，请选择MinImpact、TWAP或VWAP")
    params = supplied.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("执行参数必须是对象")
    if identifier == "minimpact":
        defaults = {"pricemode": 1, "offset": 0, "expire": 5, "span": 0, "byrate": False, "lots": 1}
        bounds = {"pricemode": (-1, 2), "offset": (-1000, 1000), "expire": (0, 86400), "span": (0, 86400000), "lots": (1, 1000000)}
    else:
        begin, finish = int(start) % 10000, int(end) % 10000
        if finish <= begin:
            minute = begin // 100 * 60 + begin % 100 + 1
            finish = minute // 60 * 100 + minute % 60
        defaults = {"begin_time": begin, "end_time": finish, "ord_sticky": 5, "tail_secs": 5,
                    "total_times": 5, "price_mode": 2, "price_offset": 0, "lots": 1, "minopenlots": 1}
        bounds = {"begin_time": (0, 2359), "end_time": (0, 2359), "ord_sticky": (0, 86400),
                  "tail_secs": (0, 86400), "total_times": (1, 10000), "price_mode": (0, 2),
                  "price_offset": (0, 1000), "lots": (1, 1000000), "minopenlots": (1, 1000000)}
    if set(params) - set(defaults):
        raise ValueError("不支持的执行参数：" + ",".join(sorted(set(params) - set(defaults))))
    effective = {**defaults, **params}
    for key, (low, high) in bounds.items():
        value = effective[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or int(value) != value:
            raise ValueError("执行参数" + key + "超出有效整数范围")
        effective[key] = int(value)
    if identifier == "minimpact":
        if effective["byrate"] is not False:
            raise ValueError("Plus的MinImpact当前使用lots固定手数，byrate须为false")
    else:
        begin, finish = effective["begin_time"], effective["end_time"]
        if begin % 100 >= 60 or finish % 100 >= 60:
            raise ValueError("执行起止时间须为HHMM")
        duration = (finish // 100 * 60 + finish % 100 - begin // 100 * 60 - begin % 100) * 60
        if duration <= 0 or effective["tail_secs"] >= duration:
            raise ValueError("执行结束须晚于开始，收尾秒数须小于窗口时长；跨午夜请拆分任务")
        effective["total_secs"] = duration
    if identifier == "vwap":
        weights = supplied.get("volume_weights")
        if not isinstance(weights, list) or not 2 <= len(weights) <= 1440 or any(
                isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in weights) or sum(weights) <= 0:
            raise ValueError("VWAP须提供2至1440个非负成交量权重，且总和大于0")
        # Weights are supplied before the replay. Never derive them from future ticks.
        effective["volume_weights"] = weights
    elif "volume_weights" in supplied:
        raise ValueError("只有VWAP使用成交量权重")
    return {**item, "params": effective}
