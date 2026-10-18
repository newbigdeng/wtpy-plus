"""Online strategy scaffolds. Callbacks match the installed wtpy strategy bases."""
from pathlib import Path

STRATEGY_TYPES = {
    "cta": {"id": "cta", "label": "CTA", "base_class": "BaseCtaStrategy", "engine": "ET_CTA",
            "description": "K线驱动 / 目标仓位；默认 causal_touch 成交模型。",
            "callbacks": ["on_init", "on_tick", "on_bar", "on_calculate", "on_calculate_done", "on_session_begin", "on_session_end", "on_backtest_end"]},
    "hft": {"id": "hft", "label": "HFT", "base_class": "BaseHftStrategy", "engine": "ET_HFT",
            "description": "逐笔事件 / 委托回报；需要与研究目标匹配的Tick数据，K线模拟Tick不能代表微观成交。",
            "callbacks": ["on_init", "on_tick", "on_bar", "on_channel_ready", "on_channel_lost", "on_entrust", "on_order", "on_trade", "on_session_begin", "on_session_end", "on_backtest_end"]},
    "sel": {"id": "sel", "label": "SEL", "base_class": "BaseSelStrategy", "engine": "ET_SEL",
            "description": "定时选品 / 目标仓位；每日09:30触发on_calculate，可在构造函数中调整调度参数。",
            "callbacks": ["on_init", "on_tick", "on_bar", "on_calculate", "on_calculate_done", "on_session_begin", "on_session_end", "on_backtest_end"]},
}

def normalize_strategy_type(value):
    value = str(value or "cta").strip().lower()
    if value not in STRATEGY_TYPES:
        raise ValueError("策略类型仅支持 CTA、HFT、SEL；当前没有 UFT Python Mocker")
    return value

def strategy_template(strategy_type):
    kind = normalize_strategy_type(strategy_type)
    base = STRATEGY_TYPES[kind]["base_class"]
    header = f'''"""{kind.upper()}策略框架：业务回调为空，不会自动产生订单。
默认合约/日期仅用于已安装的固定回放样例，研究时请改为自己的数据。
Python API 回调名以本地StrategyDefs.py为准；定时事件使用on_calculate。
"""
from wtpy import {base}


class MyStrategy({base}):
    def __init__(self, name, code="SHFE.rb.2701", period="m1"):
        super().__init__(name)
        self.code = code
        self.period = period
'''
    if kind == "sel":
        header += '''        # 由在线回测runner读取；分钟调度可改为 period="min", time=5。
        self.schedule = {"date": 0, "time": 930, "period": "d",
                         "trdtpl": "CHINA", "session": "TRADING"}
'''
    header += "\n    def on_init(self, context):\n"
    if kind == "cta":
        header += "        context.stra_get_bars(self.code, self.period, 100, isMain=True)\n"
    else:
        header += "        context.stra_prepare_bars(self.code, self.period, 100)\n        context.stra_sub_ticks(self.code)\n"
    callbacks = [
        ("on_tick", "context, stdCode, newTick", "每笔行情；实现你的策略逻辑。"),
        ("on_bar", "context, stdCode, period, newBar", "K线闭合通知。"),
    ]
    if kind in ("cta", "sel"):
        callbacks += [("on_calculate", "context", "CTA主K线 / SEL定时调度的计算回调。"),
                      ("on_calculate_done", "context", "本次计算完成。")]
    else:
        callbacks += [
            ("on_channel_ready", "context", "回测交易通道就绪。"),
            ("on_channel_lost", "context", "交易通道失去连接。"),
            ("on_entrust", "context, localid, stdCode, bSucc, msg, userTag", "委托提交结果，失败时检查msg。"),
            ("on_order", "context, localid, stdCode, isBuy, totalQty, leftQty, price, isCanceled, userTag", "委托状态变化。"),
            ("on_trade", "context, localid, stdCode, isBuy, qty, price, userTag", "成交回报。"),
            ("on_order_detail", "context, stdCode, newOrdQue", "逐笔委托，需要对应数据。"),
            ("on_order_queue", "context, stdCode, newOrdQue", "委托队列，需要对应数据。"),
            ("on_transaction", "context, stdCode, newTrans", "逐笔成交，需要对应数据。"),
        ]
    callbacks += [("on_session_begin", "context, curTDate", "交易日开始。"),
                  ("on_session_end", "context, curTDate", "交易日结束。"),
                  ("on_backtest_end", "context", "回放结束；报告由runner统一生成。")]
    for name, args, explanation in callbacks:
        header += f'\n    def {name}(self, {args}):\n        """{explanation}"""\n        pass\n'
    return header


def runner_template():
    return Path(__file__).with_name("backtest_runner.py.tpl").read_text(encoding="utf-8")
