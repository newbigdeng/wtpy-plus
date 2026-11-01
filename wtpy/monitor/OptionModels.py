"""Commodity futures option analytics. Use one valuation date per worker process."""
from bisect import bisect_left
from math import exp, isfinite, sqrt

import QuantLib as ql

DC = ql.Actual365Fixed()


def qdate(value):
    text = str(value)
    return ql.Date(int(text[6:8]), int(text[4:6]), int(text[:4]))


def contract_metrics(forward, strike, premium, days, rate, call, model, today, scenario_vol=None):
    """Greeks hold the futures quote fixed for theta/rho; return daily theta and point vega/rho."""
    if days <= 0 or min(forward, strike, premium) <= 0:
        raise ValueError("已到期或价格无效")
    side = ql.Option.Call if call else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(side, strike)
    intrinsic = payoff(forward)
    time = days / 365
    discount = exp(-rate*time)
    if model not in ("american", "black76"):
        raise ValueError("未知定价模型")
    american = model == "american"
    if premium <= intrinsic * (1 if american else discount) + 1e-9:
        raise ValueError("报价位于行权下界，无法唯一反推波动率")
    if american:
        # BlackProcess models a future with zero drift (dividend yield equals the rate).
        ql.Settings.instance().evaluationDate = today
        f, v, r = (ql.SimpleQuote(n) for n in (forward, .25, rate))
        curve = ql.YieldTermStructureHandle(ql.FlatForward(today, ql.QuoteHandle(r), DC))
        vol = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, ql.NullCalendar(), ql.QuoteHandle(v), DC))
        process = ql.BlackProcess(ql.QuoteHandle(f), curve, vol)
        engine = ql.BaroneAdesiWhaleyApproximationEngine(process)
        options = {}
        def price(fwd, sigma, d, interest):
            if d == 0:
                return payoff(fwd)
            if interest == 0:
                return ql.blackFormula(side, strike, fwd, sigma*sqrt(d/365))
            if d not in options:
                options[d] = ql.VanillaOption(payoff, ql.AmericanExercise(today, today+d))
                options[d].setPricingEngine(engine)
            f.setValue(fwd)
            v.setValue(sigma)
            r.setValue(interest)
            return options[d].NPV()
    else:
        def price(fwd, sigma, d, interest):
            return ql.blackFormula(side, strike, fwd, sigma*sqrt(d/365), exp(-interest*d/365))

    def residual(sigma):
        return price(forward, sigma, days, rate) - premium
    # A very small input volatility can make the BAW critical-price iteration singular.
    # Price at zero volatility equals the applicable discounted/intrinsic bound for r >= 0.
    def objective(sigma):
        if sigma == 0:
            return intrinsic * (1 if american else discount) - premium
        return residual(sigma)
    iv = ql.Brent().solve(objective, 1e-8, .25, 0., 5.)
    base = price(forward, iv, days, rate)
    h = forward * .0001
    up, down = (price(forward+b, iv, days, rate) for b in (h,-h))
    dv = min(.0001, iv/2)
    vega = (price(forward, iv+dv, days, rate)-price(forward, iv-dv, days, rate))/(2*dv)
    rlo, rhi = max(0., rate-.0001), rate+.0001
    rho = (price(forward, iv, days, rhi)-price(forward, iv, days, rlo))/(rhi-rlo)
    values = dict(iv=iv, delta=(up-down)/(2*h), gamma=(up+down-2*base)/(h*h),
                  theta=price(forward,iv,days-1,rate)-base, vega=vega/100, rho=rho/100,
                  theoretical=base)
    if scenario_vol is not None:
        values["scenario_price"] = price(forward, scenario_vol, days, rate)
    if not all(isfinite(v) for v in values.values()):
        raise ValueError("定价引擎未得到有限解")
    return values


def interpolate(points, x):
    """Linear interpolation only inside observed support."""
    points = sorted(points)
    idx = bisect_left(points, (x,))
    if idx < len(points) and points[idx][0] == x:
        return points[idx][1]
    if idx == 0 or idx == len(points):
        return None
    a, va = points[idx-1]
    b, vb = points[idx]
    return va + (vb-va)*(x-a)/(b-a)


def smile_summary(rows):
    smiles = {side: [r for r in rows if r["side"]==side] for side in ("看涨","看跌")}
    result = {}
    for side, key in (("看涨","call"),("看跌","put")):
        strip = smiles[side]
        if strip:
            variance = interpolate([(r["strike"], r["iv"]**2) for r in strip], strip[0]["forward"])
            if variance is not None:
                result[key+"_atm"] = sqrt(variance)
    matched = {r["strike"]:r for r in smiles["看跌"]}
    weighted = []
    for left in smiles["看涨"]:
        right = matched.get(left["strike"])
        if right and (weight:=min(left["vega"],right["vega"])) > 0:
            weighted.append((left["iv"]-right["iv"],weight))
    if weighted:
        result["weighted_gap"] = sum(g*w for g,w in weighted)/sum(w for _,w in weighted)
    if "call_atm" in result and "put_atm" in result:
        result["atm_gap"] = result["call_atm"]-result["put_atm"]
    for target in (.10,.25):
        values = [interpolate([(abs(r["delta"]),r["iv"]) for r in strip], target) for strip in smiles.values()]
        if all(v is not None for v in values):
            result[f"gap_{int(100*target)}d"] = values[0]-values[1]
    return result


def variance_surface(rows, today, query=None):
    """Build one QuantLib surface for a single product and side, on common strike support."""
    strips, owners = {}, {}
    for row in rows:
        owner = row.get("underlying")
        if row["expiry"] in owners and owners[row["expiry"]]!=owner:
            return {"message":"同一到期日对应不同标的，无法合并曲面"}
        owners[row["expiry"]] = owner
        strips.setdefault(row["expiry"], []).append((row["strike"], row["iv"]**2))
    expiries = sorted(strips)
    if len(expiries)<2:
        return {"message":"至少选择两个到期日"}
    lo = max(min(k for k,_ in strip) for strip in strips.values())
    hi = min(max(k for k,_ in strip) for strip in strips.values())
    strikes = sorted({k for strip in strips.values() for k,_ in strip if lo<=k<=hi})
    if len(strikes)<2:
        return {"message":"各期限至少需要两个重叠行权价"}
    dates = [qdate(e) for e in expiries]
    vols = [[sqrt(interpolate(strips[e], k)) for e in expiries] for k in strikes]
    surface = ql.BlackVarianceSurface(today, ql.NullCalendar(), dates, strikes, ql.Matrix(vols), DC)
    data = dict(expiries=expiries,strikes=strikes,iv=vols)
    if query:
        k, date = query
        if lo<=k<=hi and dates[0]<=date<=dates[-1]:
            data["query_iv"] = surface.blackVol(date,k)
    return data


def kirk_price(f1, f2, v1, v2, strike, days, rate, correlation, ratio, today):
    """European call on F1 - ratio*F2, after unit conversion, at the common horizon."""
    if not -1<=correlation<=1 or min(f1,f2,v1,v2,ratio,days)<=0 or ratio*f2+strike<=0:
        raise ValueError("价差输入超出 Kirk 模型范围")
    ql.Settings.instance().evaluationDate = today
    discount = ql.YieldTermStructureHandle(ql.FlatForward(today,rate,DC))
    def process(f, sigma):
        vol = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today,ql.NullCalendar(),sigma,DC))
        return ql.BlackProcess(ql.QuoteHandle(ql.SimpleQuote(f)),discount,vol)
    option = ql.SpreadOption(ql.PlainVanillaPayoff(ql.Option.Call,strike),ql.EuropeanExercise(today+days))
    option.setPricingEngine(ql.KirkSpreadOptionEngine(process(f1,v1),process(ratio*f2,v2),
                                                     ql.QuoteHandle(ql.SimpleQuote(correlation))))
    result = option.NPV()
    if not isfinite(result):
        raise ValueError("Kirk 无有限解")
    return result
