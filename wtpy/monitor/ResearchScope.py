"""Cheap research-horizon routing before downloads and model calls."""
import json
from pathlib import Path
import re

VERSION = 'intraday-and-5d-v1'
INTRADAY = re.compile(r'\bintraday\b|\bintra-day\b|\bminute[- ](?:level|frequency|bars?)\b|日内|分钟(?:级|频|线)', re.I)
LONG = re.compile(r'\b(?:monthly|quarterly|annual)\s+(?:factor|return|rebalanc|portfolio|asset|data|frequen)|'
                  r'\b(?:month|quarter|year)[- ]ahead\b|\blong[- ](?:term|horizon)\s+(?:invest|factor|asset|return)|'
                  r'月频|季频|年频|月度(?:因子|收益|调仓)|季度调仓|年度调仓|长期(?:因子|投资|资产配置)', re.I)
METHOD = re.compile(r'backtest(?:ing)?\s+overfit|deflated\s+sharpe|purged\s+cross.validation|'
                    r'look.ahead\s+bias|data\s+leakage|transaction\s+cost|factor\s+(?:decay|correlation)|'
                    r'过拟合|数据泄漏|未来函数|交易成本|因子衰减|因子相关性', re.I)
HOLDING = re.compile(r'(?:holding\s+(?:period|horizon)|hold(?:ing)?\s+(?:for|positions?\s+for)|'
                     r'forecast(?:ing)?\s+horizon|predict(?:ing)?\s+(?:the\s+)?next|'
                     r'持有(?:期|期限)?|预测(?:期|期限))'
                     r'\s*(?:of|is|=|:|为|是|约)?\s*(\d+|one|two|three|four|five|一|二|三|四|五|十|二十)'
                     r'\s*(?:个|trading\s+|business\s+)?(minutes?|hours?|days?|weeks?|months?|years?|分钟|小时|交易日|天|周|月|年)', re.I)
NUMBERS = dict(one=1, two=2, three=3, four=4, five=5, 一=1, 二=2, 三=3, 四=4, 五=5, 十=10, 二十=20)


def load_scope(root):
    path = Path(root) / 'research-scope.json'
    return json.loads(path.read_text()) if path.is_file() else {}


def classify(source, policy):
    if not policy.get('enabled'):
        return dict(category='unspecified', excluded=False, reason='', version='disabled')
    metadata = '\n'.join(str(source.get(key) or '') for key in ('title', 'abstract'))
    body = str(source.get('body') or '')
    maximum = policy.get('max_holding_trading_days', 5)
    title = str(source.get('title') or '')
    def result(category, reason):
        return dict(category=category, excluded=category == 'long_horizon', reason=reason, version=VERSION)
    # Method papers remain references, but do not become automatic trade candidates.
    if policy.get('keep_methodology', True) and METHOD.search(title):
        return result('methodology', '保留为成本、风控或研究检验参考')
    periods = []
    for match in HOLDING.finditer(metadata + '\n' + body):
        number, unit = match.groups()
        count = float(number) if number.isdigit() else NUMBERS[number.lower()]
        multiplier = (0 if re.match(r'minute|hour|分钟|小时', unit, re.I) else
                      252 if re.match(r'year|年', unit, re.I) else
                      21 if re.match(r'month|月', unit, re.I) else
                      5 if re.match(r'week|周', unit, re.I) else 1)
        periods.append(count * multiplier)
    if periods and min(periods) <= maximum:
        return result('intraday' if min(periods) == 0 else 'short_term', '原文包含日内或五个交易日以内的预测、持有期限')
    if INTRADAY.search(metadata):
        # An explicit long holding period still wins over minute-resolution input data.
        if not periods:
            return result('intraday', '标题或摘要明确研究日内、分钟信号')
    if periods or LONG.search(metadata):
        return result('long_horizon', '原文明确长期持有、预测或月季年频研究，超出日内至五日范围')
    cached = source.get('research_scope', {})
    if not body and cached.get('version') == VERSION and cached.get('excluded'):
        return dict(cached)
    return result('unspecified', '期限尚不明确；周报发布时间、回看窗口不等于持有期')


def priority(decision):
    return {'intraday': 0, 'short_term': 1, 'unspecified': 2, 'methodology': 3, 'long_horizon': 4}[decision['category']]
