"""Small, explicit research calculators. Inputs are scenarios, never orders."""
import math
import threading

import numpy as np
import numpy_financial as npf
from scipy.optimize import minimize
import QuantLib as ql

QL_LOCK = threading.Lock()  # QuantLib evaluationDate is process-global.


def field(key, label, default, kind='number'):
    return dict(key=key, label=label, default=default, kind=kind)


TOOLS = [
    dict(id='risk', name='收益与风险', group='组合', engine='NumPy / fincore',
         note='输入已扣费的等间隔简单收益。年化频率须与数据一致。', fields=[
             field('returns', '收益序列（小数，逗号分隔）', '', 'text'),
             field('periods', '每年观测次数', 252), field('confidence', 'VaR 置信度', .95),
             field('capital', '分析本金', 10000), field('risk_free', '年化无风险利率', .02)]),
    dict(id='optimize', name='组合优化', group='组合', engine='SciPy SLSQP',
         note='每行一个日期，每列一个资产。先在训练期估计，再用独立样本验证。', fields=[
             field('matrix', '对齐收益矩阵（逗号分列，换行分日期）', '', 'text'),
             field('objective', '目标', 'min_variance', 'objective'),
             field('periods', '每年观测次数', 252), field('max_weight', '单资产权重上限', 1),
             field('risk_free', '年化无风险利率', .02)]),
    dict(id='dcf', name='现金流估值 · DCF', group='估值', engine='NumPy Financial',
         note='年度自由现金流从第 1 年末开始。现金流、债务与股数使用同一量级。', fields=[
             field('cashflows', '未来年度自由现金流', '100,110,120,130,140', 'text'),
             field('discount', '年折现率', .10), field('growth', '永续增长率', .02),
             field('debt', '净债务', 100), field('shares', '股数', 100)]),
    dict(id='cashflow', name='项目 / 另类资产', group='估值', engine='NumPy Financial',
         note='首项为今天投入，之后为等间隔现金流。IRR 可能没有唯一解。', fields=[
             field('cashflows', '现金流（首项含投入）', '-1000,150,200,250,600', 'text'),
             field('discount', '每期折现率', .08), field('finance_rate', '融资利率', .08),
             field('reinvest_rate', '再投资利率', .06)]),
]

OPTION_FIELDS = [field('forward', '远期价格 / 标的价格', 100), field('strike', '执行价', 100),
                 field('volatility', '年化波动率（小数）', .2), field('years', '剩余年数', 1),
                 field('rate', '连续复利利率', .03), field('side', '方向', 'call', 'side')]
RATE_OPTION_FIELDS = [field('forward', '远期利率（小数）', .03), field('strike', '执行利率（小数）', .03),
                      *OPTION_FIELDS[2:]]
QL_TOOLS = [
    ('black76', '期货期权 · Black76', '远期价格为期货价格。无保证金和成交费用。', OPTION_FIELDS),
    ('bachelier', '正态期权 · Bachelier', '波动率为价格单位/年平方根，并非百分比。', [dict(f, default=20) if f['key']=='volatility' else f for f in OPTION_FIELDS]),
    ('black_scholes', '欧式现货期权', '使用现货价格和连续股息率。', OPTION_FIELDS + [field('dividend', '连续股息率', 0)]),
    ('american', '美式期权', 'CRR 二叉树，固定 300 步；可提前行权。', OPTION_FIELDS + [field('dividend', '连续股息率', 0)]),
    ('implied_vol', '期货期权隐含波动率', '从市场权利金反解 Black76 波动率。', OPTION_FIELDS + [field('premium', '市场权利金', 7.730149)]),
    ('digital', '现金二元期权', '欧式到期触发支付；不包含交易摩擦。', OPTION_FIELDS + [field('cash', '到期支付金额', 1)]),
    ('barrier', '障碍期权', '连续观察、向下敲出、零返还；不是离散触碰模拟。', OPTION_FIELDS + [field('barrier', '向下敲出价格', 80),field('dividend', '连续股息率', 0)]),
    ('asian', '几何平均亚式期权', '连续观察的几何平均，不代表算术平均合约。', OPTION_FIELDS + [field('dividend', '连续股息率', 0)]),
    ('bond', '固定利率债券', '以期初面值和规则票息期定价。无违约与应计利息。', [field('face', '面值', 100), field('coupon', '年票息率', .04),field('yield', '年到期收益率', .035),field('years','年限（整数）',5),field('frequency','每年付息次数',2)]),
    ('zero_bond', '零息债券', '按连续复利曲线折现。', [field('face','面值',100),field('years','年限',5),field('rate','连续复利利率',.03)]),
    ('curve', '零息 / 远期曲线', '平坦连续复利曲线，用于情景估值。', [field('rate','连续复利利率',.03),field('years','最长期限（整数）',10)]),
    ('forward', '持有成本远期', '净持有成本包括融资、仓储和便利收益。', [field('spot','现货价格',100),field('rate','融资利率',.03),field('storage','仓储成本率',.01),field('convenience','便利收益率',.005),field('years','年限',1)]),
    ('fx_forward', '外汇远期', '报价为每单位外币的本币价格。', [field('spot','即期汇率',7.1),field('domestic','本币利率',.02),field('foreign','外币利率',.04),field('years','年限',1)]),
    ('swap', '利率互换', '单曲线、规则半年付息，固定端支付方。', [field('notional','名义本金',1000000),field('fixed','固定利率',.035),field('rate','平坦零息利率',.03),field('years','年限（整数）',5)]),
    ('swaption', '欧式互换期权', '年金为今日现值，已含折现。结果按单位名义本金计算。', RATE_OPTION_FIELDS + [field('annuity','互换年金现值',4.5)]),
    ('caplet', '利率上限 / 下限单期', '到期确定利率，计息期末支付。看涨为上限，看跌为下限。', RATE_OPTION_FIELDS + [field('notional','名义本金',1000000),field('accrual','计息年数',.5)]),
    ('credit', '信用存续曲线', '恒定违约强度模型，不是 CDS 市场校准。', [field('hazard','年违约强度',.02),field('recovery','回收率',.4),field('years','年限（整数）',5)]),
    ('calendar', '交易日与计息', '中国上交所日历只作日期工具，不代替期货柜台日历。', [field('start','开始日期','2026-10-30','date'),field('end','结束日期','2027-01-30','date')]),
]
TOOLS += [dict(id=i,name=n,group='QuantLib',engine='QuantLib '+ql.__version__,note=note,fields=fields) for i,n,note,fields in QL_TOOLS]


def vector(value):
    if isinstance(value, str):
        value = value.replace('，', ',').replace('\n', ',').split(',')
        value = [x.strip() for x in value if x.strip()]
    result = np.asarray(value, dtype=float)
    if result.ndim != 1 or not 1 <= len(result) <= 10000 or not np.isfinite(result).all():
        raise ValueError('请输入 1 至 10000 个有限数值')
    return result


def number(p, key, low=-1e12, high=1e12):
    value = float(p[key])
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(key + ' 超出范围')
    return value


def risk(p):
    r = vector(p['returns'])
    if len(r) < 20 or np.any(r <= -1):
        raise ValueError('至少需要 20 个有效简单收益，且每期大于 -100%')
    periods = number(p, 'periods', 1, 525600)
    confidence = number(p, 'confidence', .8, .999)
    capital = number(p, 'capital', 1)
    rf = number(p, 'risk_free', -.99, 10)
    excess = r - ((1 + rf) ** (1 / periods) - 1)
    sd = float(np.std(excess, ddof=1))
    from fincore.metrics.drawdown import max_drawdown
    from fincore.metrics.returns import cum_returns_final
    loss = -r
    var = float(np.quantile(loss, confidence))
    tail = loss[loss >= var]
    wealth = capital * np.cumprod(1 + r)
    return dict(metrics={'观测数':len(r),'年化频率':periods,'累计收益':float(cum_returns_final(r)),
        '年化波动率':float(np.std(r,ddof=1)*np.sqrt(periods)),
        '夏普比率':float(np.mean(excess)/sd*np.sqrt(periods)) if sd else None,
        '最大回撤':float(max_drawdown(r)), '单期 VaR':var, '单期 CVaR':float(tail.mean()),
        'VaR 金额':capital*var, 'CVaR 金额':capital*float(tail.mean())},
        series=[dict(time=i+1,value=float(v)) for i,v in enumerate(wealth)],
        note='历史分位风险；收益已扣费由输入方保证。年化假设各期可比较，样本不证明未来收益。')


def optimize(p):
    raw = p['matrix']
    a = np.asarray([vector(line) for line in raw.strip().splitlines() if line.strip()] if isinstance(raw,str) else raw, dtype=float)
    if a.ndim != 2 or not 20 <= len(a) <= 10000 or not 2 <= a.shape[1] <= 20 or not np.isfinite(a).all() or np.any(a <= -1):
        raise ValueError('需要 20 至 10000 行、2 至 20 列的同日期收益矩阵')
    cap = number(p,'max_weight',.01,1);n=a.shape[1]
    if cap*n < 1-1e-10:raise ValueError('权重上限无法组成 100% 的组合')
    periods=number(p,'periods',1,525600);rf=number(p,'risk_free',-.99,10)
    cov=np.cov(a,rowvar=False)*periods;mean=a.mean(axis=0)*periods
    objective=p['objective']
    if objective not in ('min_variance','max_sharpe'):raise ValueError('未知优化目标')
    def loss(w):
        variance=float(w@cov@w)
        return variance if objective=='min_variance' else -(float(w@mean)-rf)/math.sqrt(max(variance,1e-16))
    result=minimize(loss,np.full(n,1/n),method='SLSQP',bounds=[(0,cap)]*n,
                    constraints=[{'type':'eq','fun':lambda w:w.sum()-1}],options={'maxiter':300,'ftol':1e-12})
    if not result.success:raise ValueError('优化没有收敛：'+result.message)
    weights=result.x;sd=math.sqrt(max(float(weights@cov@weights),0))
    return dict(metrics={'样本数':len(a),'资产数':n,'年化均值':float(weights@mean),'年化波动率':sd,
                         '样本内夏普':float((weights@mean-rf)/sd) if sd else None},
                rows=[dict(asset='资产 '+str(i+1),weight=float(w)) for i,w in enumerate(weights)],
                note='仅做多、权重合计 100%。这是样本内配置，不自动调整 WT 持仓。')


def ql_calculate(tool, p):
    if tool in ('black76','bachelier','black_scholes','american','implied_vol','digital','barrier','asian','swaption','caplet'):
        f=number(p,'forward',1e-8);k=number(p,'strike',1e-8);vol=number(p,'volatility',1e-8,1e4)
        years=number(p,'years',1/365,100);rate=number(p,'rate',-1,2)
        if p['side'] not in ('call','put'):raise ValueError('方向必须为 call 或 put')
        side=ql.Option.Call if p['side']=='call' else ql.Option.Put
        discount=math.exp(-rate*years);stddev=vol*math.sqrt(years)
        if tool=='swaption':discount=1.0  # The input annuity is already a present value.
        if tool=='caplet':discount=math.exp(-rate*(years+number(p,'accrual',0,10)))
        if tool=='implied_vol':
            iv=ql.blackFormulaImpliedStdDev(side,k,f,number(p,'premium',0),discount,0,stddev,1e-10,200)/math.sqrt(years)
            return dict(metrics={'隐含波动率':iv})
        if tool=='bachelier':return dict(metrics={'权利金':ql.bachelierBlackFormula(side,k,f,stddev,discount)})
        if tool in ('black76','swaption','caplet','digital'):
            if tool=='digital':
                value=discount*number(p,'cash',0)*ql.blackFormulaCashItmProbability(side,k,f,stddev)
                return dict(metrics={'权利金':value})
            calc=ql.BlackCalculator(ql.PlainVanillaPayoff(side,k),f,stddev,discount)
            scale=number(p,'annuity',0) if tool=='swaption' else number(p,'notional',0)*number(p,'accrual',0,10) if tool=='caplet' else 1
            return dict(metrics={'权利金':calc.value()*scale,'远期 Delta':calc.deltaForward()*scale,
                                 'Vega（波动率增加 1%）':calc.vega(years)*scale/100},
                        series=[dict(time=round(float(price),6),value=ql.blackFormula(side,k,float(price),stddev,discount)*scale)
                                for price in np.linspace(f*.7,f*1.3,31)],
                        note='曲线展示远期价格变化时的理论权利金，其他输入保持不变。')
        today=ql.Date(30,10,2026);ql.Settings.instance().evaluationDate=today
        expiry=today+max(1,round(years*365));day=ql.Actual365Fixed()
        process=ql.BlackScholesMertonProcess(ql.QuoteHandle(ql.SimpleQuote(f)),
            ql.YieldTermStructureHandle(ql.FlatForward(today,number(p,'dividend',-1,2),day)),
            ql.YieldTermStructureHandle(ql.FlatForward(today,rate,day)),
            ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today,ql.NullCalendar(),vol,day)))
        payoff=ql.PlainVanillaPayoff(side,k)
        exercise=ql.AmericanExercise(today,expiry) if tool=='american' else ql.EuropeanExercise(expiry)
        if tool=='barrier':
            barrier=number(p,'barrier',1e-8,f)
            option=ql.BarrierOption(ql.Barrier.DownOut,barrier,0,payoff,exercise)
            engine=ql.AnalyticBarrierEngine(process)
        elif tool=='asian':
            option=ql.ContinuousAveragingAsianOption(ql.Average.Geometric,payoff,exercise)
            engine=ql.AnalyticContinuousGeometricAveragePriceAsianEngine(process)
        else:
            option=ql.VanillaOption(payoff,exercise)
            engine=ql.BinomialVanillaEngine(process,'crr',300) if tool=='american' else ql.AnalyticEuropeanEngine(process)
        option.setPricingEngine(engine)
        return dict(metrics={'权利金':option.NPV(),'实际计息年数':day.yearFraction(today,expiry)})
    if tool=='bond':
        face=number(p,'face',.01);coupon=number(p,'coupon',0,2);y=number(p,'yield',-.9,2)
        years=number(p,'years',1,50);freq=number(p,'frequency',1,12)
        if years!=int(years) or freq not in (1,2,4,12):raise ValueError('年限须为整数，付息次数为 1、2、4 或 12')
        count=int(years*freq);times=np.arange(1,count+1)/freq
        flows=np.full(count,face*coupon/freq);flows[-1]+=face
        rate=ql.InterestRate(y,ql.Actual365Fixed(),ql.Compounded,int(freq))
        pv=np.array([flow*rate.discountFactor(float(t)) for flow,t in zip(flows,times)])
        price=float(pv.sum());duration=float(times@pv/price)
        return dict(metrics={'价格':price,'麦考利久期':duration,'修正久期':duration/(1+y/freq)},
                    rows=[dict(year=float(t),cashflow=float(c),present_value=float(v)) for t,c,v in zip(times,flows,pv)])
    if tool in ('zero_bond','curve','forward','fx_forward','swap','credit'):
        years=number(p,'years',.001,100)
        if tool=='forward':return dict(metrics={'理论远期价格':number(p,'spot',.001)*math.exp((number(p,'rate',-1,2)+number(p,'storage',-1,2)-number(p,'convenience',-1,2))*years)})
        if tool=='fx_forward':return dict(metrics={'理论远期汇率':number(p,'spot',.001)*math.exp((number(p,'domestic',-1,2)-number(p,'foreign',-1,2))*years)})
        if tool=='credit':
            hazard=number(p,'hazard',0,5);recovery=number(p,'recovery',0,1)
            curve=ql.FlatHazardRate(ql.Date(30,10,2026),ql.QuoteHandle(ql.SimpleQuote(hazard)),ql.Actual365Fixed())
            return dict(metrics={'存续概率':curve.survivalProbability(years),'预期损失率':(1-curve.survivalProbability(years))*(1-recovery)})
        rate=number(p,'rate',-1,2);curve=ql.FlatForward(ql.Date(30,10,2026),rate,ql.Actual365Fixed())
        if tool=='zero_bond':return dict(metrics={'现值':number(p,'face',.01)*curve.discount(years)})
        if tool=='curve':return dict(rows=[dict(year=i,discount=curve.discount(float(i)),zero_rate=curve.zeroRate(float(i),ql.Continuous).rate()) for i in range(1,int(years)+1)])
        if years!=int(years):raise ValueError('互换年限须为整数')
        annuity=sum(.5*curve.discount(i/2) for i in range(1,int(years*2)+1))
        par=(1-curve.discount(years))/annuity
        return dict(metrics={'平价固定利率':par,'年金':annuity,'固定端支付方现值':number(p,'notional',1)*annuity*(par-number(p,'fixed',-1,2))})
    if tool=='calendar':
        start=ql.DateParser.parseISO(p['start']);end=ql.DateParser.parseISO(p['end'])
        if end<=start:raise ValueError('结束日期须晚于开始日期')
        return dict(metrics={'Actual/365':ql.Actual365Fixed().yearFraction(start,end),'Actual/360':ql.Actual360().yearFraction(start,end),
                             '上交所工作日数':ql.China(ql.China.SSE).businessDaysBetween(start,end)},
                    note='此处固定使用 QuantLib 已知日历；不用于替换 WT 的交易日与夜盘规则。')
    raise ValueError('未知 QuantLib 工具')


def calculate(tool, params):
    definition=next((x for x in TOOLS if x['id']==tool),None)
    if not definition:raise ValueError('未知分析工具')
    p={f['key']:f['default'] for f in definition['fields']};p.update(params)
    if tool=='risk':result=risk(p)
    elif tool=='optimize':result=optimize(p)
    elif tool in ('dcf','cashflow'):
        flows=vector(p['cashflows']);discount=number(p,'discount',-.9,5)
        if tool=='dcf':
            growth=number(p,'growth',-.9,2)
            if growth>=discount:raise ValueError('永续增长率必须低于折现率')
            terminal=flows[-1]*(1+growth)/(discount-growth)
            pv=float(npf.npv(discount,[0,*flows]));tpv=terminal/(1+discount)**len(flows)
            equity=pv+tpv-number(p,'debt')
            result=dict(metrics={'预测期现值':pv,'终值现值':tpv,'企业价值':pv+tpv,'股权价值':equity,'每股价值':equity/number(p,'shares',.0001)})
        else:
            irr=float(npf.irr(flows));mirr=float(npf.mirr(flows,number(p,'finance_rate',-.9,5),number(p,'reinvest_rate',-.9,5)))
            result=dict(metrics={'净现值':float(npf.npv(discount,flows)),'内部收益率':irr if math.isfinite(irr) else None,'修正内部收益率':mirr if math.isfinite(mirr) else None})
    else:
        with QL_LOCK:
            previous=ql.Settings.instance().evaluationDate
            try:result=ql_calculate(tool,p)
            finally:ql.Settings.instance().evaluationDate=previous
    return dict(tool=tool,engine=definition['engine'],**result)
