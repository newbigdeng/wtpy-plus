"""Research records and native CTA experiments, separate from live execution."""
from __future__ import annotations
import base64
from contextlib import contextmanager
from datetime import datetime
import html
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import uuid

from .HistoryCatalog import dataset_entries, public_dataset, selection_bounds
from .Roles import group_allowed

TERMINAL = frozenset(('completed', 'failed', 'stopped', 'interrupted'))
SEED_REPORTS = [{
    'id':'public-htfc-cta-20171124', 'title':'CTA量化策略因子系列（四）：策略因子组合与择时',
    'organization':'华泰期货研究院', 'published_at':'2017-11-24',
    'source_url':'https://www.htfc.com/main/a/20171124/80037391.shtml',
    'body':'报告讨论动量、期限结构和波动率信号，以及不同信号的组合与择时。阅读原文后，先登记经济假设、公式、调仓时点和数据需求，再建立实验。',
    'notes':'随附双均线模板用于验证趋势研究流程，并非原报告全部模型或历史收益的逐图复现。',
    'public':True, 'attachment_url':None,
}]
TEMPLATES = [{
    'id':'dual_ma', 'label':'双均线趋势 · 方法验证',
    'description':'快均线高于慢均线时持有多头，反之持有空头；使用已闭合K线。用于跑通研报思路的实验流程。',
    'formula':'target = lots × sign(SMA(close, fast) − SMA(close, slow))',
    'params':{'fast':5,'slow':20,'lots':1},
    'bounds':{'fast':[2,200],'slow':[3,500],'lots':[1,20]},
}]


def template_params(value=None):
    result = dict(TEMPLATES[0]['params'])
    value = value or {}
    if not isinstance(value, dict) or set(value)-set(result):
        raise ValueError('模板参数只支持 fast、slow、lots')
    for name, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (int,float)) or int(item)!=item:
            raise ValueError('模板参数必须为整数')
        result[name] = int(item)
    for name, bounds in TEMPLATES[0]['bounds'].items():
        if not bounds[0] <= result[name] <= bounds[1]:
            raise ValueError(name+' 超出模板范围')
    if result['fast'] >= result['slow']:
        raise ValueError('慢均线周期必须大于快均线')
    return result


def template_source(params=None):
    params = template_params(params)
    return '''"""双均线趋势方法验证；原生成交和费用由 WT 计算。"""
import json
from pathlib import Path
from wtpy import BaseCtaStrategy

class ResearchTrend(BaseCtaStrategy):
    def __init__(self, name, code="SHFE.rb.2701", period="m1"):
        super().__init__(name)
        self.code, self.period = code, period
        self.fast, self.slow, self.lots = FAST, SLOW, LOTS
        self.diagnostic = dict(calculations=0, warmup_skips=0, long_signals=0,
                               short_signals=0, target_changes=0, samples=[])

    def on_init(self, context):
        context.stra_get_bars(self.code, self.period, self.slow+1, isMain=True)

    def on_calculate(self, context):
        self.diagnostic['calculations'] += 1
        bars = context.stra_get_bars(self.code, self.period, self.slow+1)
        if bars is None or len(bars) < self.slow:
            self.diagnostic['warmup_skips'] += 1
            return
        closes = bars.closes
        fast, slow = float(sum(closes[-self.fast:])/self.fast), float(sum(closes[-self.slow:])/self.slow)
        target = self.lots if fast > slow else -self.lots if fast < slow else 0
        self.diagnostic['long_signals' if target > 0 else 'short_signals'] += int(target != 0)
        if context.stra_get_position(self.code) != target:
            context.stra_set_position(self.code, target, 'research_ma')
            self.diagnostic['target_changes'] += 1
        self.diagnostic['samples'].append(dict(time=context.stra_get_date()*10000+context.stra_get_time(), fast=fast, slow=slow, target=target))
        self.diagnostic['samples'] = self.diagnostic['samples'][-200:]

    def on_backtest_end(self, context):
        Path('research-diagnostics.json').write_text(json.dumps(self.diagnostic), encoding='utf-8')
'''.replace('FAST', str(params['fast'])).replace('SLOW', str(params['slow'])).replace('LOTS', str(params['lots']))


def source_url(value):
    value = str(value or '').strip()
    if value:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('原文链接仅支持不带登录信息的 HTTP 或 HTTPS 地址')
    if len(value)>2000:
        raise ValueError('原文链接过长')
    return value


def text_field(value, label, limit=200000, required=False):
    value = str(value or '').strip()
    if len(value)>limit or (required and not value):
        raise ValueError(label+'为空或过长')
    return value


def finite(value):
    if isinstance(value, dict):
        return {key:finite(item) for key,item in value.items()}
    if isinstance(value, list):
        return [finite(item) for item in value]
    if isinstance(value,float) and not math.isfinite(value):
        return None
    return value


class ResearchWorkspace:
    def __init__(self, root, bt_mon, workspace='/workspace'):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root,0o700)
        self.bt_mon, self.workspace = bt_mon, Path(workspace).resolve()
        self.dbfile = self.root/'research.sqlite3'
        self.lock = threading.RLock()
        self.closing = False
        with self._db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT PRIMARY KEY,owner TEXT,data TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS records_owner ON records(kind,owner)')
        from .ResearchReports import ResearchReports
        from .ResearchFactors import ResearchFactors
        self.reports=ResearchReports(self)
        self.factors=ResearchFactors(self)
        from .ResearchSourceSummary import ResearchSourceSummaries
        self.summaries=ResearchSourceSummaries(self)
        for item in self._rows('experiment') if bt_mon is not None else []:
            if item['status'] not in TERMINAL and not item.get('native_id'):
                item.update(status='interrupted',error='服务重启中断了准备阶段，输入已保留，可重新提交')
                self._save('experiment',item['owner'],item)
        for item in self._rows('factor_experiment') if bt_mon is not None else []:
            if item['status']=='running':
                item.update(status='interrupted',error='研究服务重启；实验输入已保留，可以重新运行')
                self._save('factor_experiment',item['owner'],item)
        for item in self._rows('research_workflow') if bt_mon is not None else []:
            if item['status'] in ('comparing','testing'):
                item.update(status='interrupted',error='研究服务重启，固定输入已保留，请建立新检查继续迭代')
                self._save('research_workflow',item['owner'],item)

    @contextmanager
    def _db(self):
        connection=sqlite3.connect(self.dbfile,timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _rows(self, kind, owner=None):
        with self._db() as db:
            rows = db.execute('SELECT data FROM records WHERE kind=?'+(' AND owner=?' if owner is not None else ''),
                              (kind,owner) if owner is not None else (kind,)).fetchall()
        return sorted([json.loads(row[0]) for row in rows], key=lambda row:row.get('created_at',0), reverse=True)

    def _get(self, kind, owner, identifier):
        if kind=='report':
            if str(identifier).startswith('official-'):
                return self.reports.get(identifier)
            public = next((dict(row) for row in SEED_REPORTS if row['id']==identifier), None)
            if public:
                return public
        with self._db() as db:
            row = db.execute('SELECT data FROM records WHERE kind=? AND owner=? AND id=?',(kind,owner,str(identifier))).fetchone()
        if not row:
            raise ValueError('记录不存在或不属于当前用户')
        return json.loads(row[0])

    def _save(self, kind, owner, item):
        item['updated_at'] = time.time()
        item.setdefault('created_at', item['updated_at'])
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',
                       (kind,item['id'],owner,json.dumps(finite(item),ensure_ascii=False,allow_nan=False)))
        return item

    def datasets(self, user):
        return [public_dataset(row) for row in dataset_entries(self.workspace,periods=('m1','m5','d'),
                    group_filter=lambda group:group_allowed(user,group))
                if group_allowed(user,row['groupid']) and row.get('rows') and row['period'] in ('m1','m5','d')]

    def catalog(self, user):
        return dict(datasets=self.datasets(user),templates=TEMPLATES,lake=self.lake_catalog())

    def list(self, user, include_catalog=True):
        owner=user['loginid']
        guest=user.get('role')=='guest'
        experiments=[]
        if not guest:
            for item in self._rows('experiment',owner):
                item=self._sync(owner,item)
                experiments.append({key:value for key,value in item.items() if key not in ('project_snapshot','report_snapshot')})
        result=dict(reports=self.reports.reports()+[dict(row) for row in SEED_REPORTS]+([] if guest else self._rows('report',owner)),
                    projects=[] if guest else self._rows('project',owner), experiments=experiments)
        result.update(report_sources=self.reports.sources(),report_filters=self.reports.filters())
        result['reports']=self.summaries.enrich(owner,result['reports'],compact=True)
        if not guest:
            result.update(self.factors.list(owner))
            from .ResearchAutomationStatus import automation_status
            result['factor_automation'] = automation_status(self, owner)
        if include_catalog:
            result.update(self.catalog(user))
        return result

    def lake_catalog(self):
        path=Path(os.environ.get('WT_RESEARCH_LAKE_CATALOG',str(self.root/'lake-catalog.json')))
        if not path.is_file():
            return dict(state='not_prepared',datasets=[],updated_at=None,
                        message='数据湖覆盖汇总尚未准备；可继续使用 WT 已入库历史。')
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        rows=[]
        for original in value.get('datasets',[]):
            row={key:original.get(key) for key in ('symbol','frequency','rows','start','end','dataset')}
            row['period']={'1m':'m1','5m':'m5','1d':'d','daily':'d'}.get(row['frequency'],row['frequency'])
            rows.append(row)
        return dict(state='ready',datasets=rows,updated_at=value.get('updated_at'),
                    query_method=value.get('query_method','既有 CNEquity 环境只读汇总'),
                    message=value.get('message','数据湖与 WT 覆盖分别核对；实验使用所选 WT 历史快照。'))

    def save_report(self, owner, value):
        identifier=value.get('id')
        item=self._get('report',owner,identifier) if identifier else {'id':uuid.uuid4().hex}
        if item.get('public') or item.get('readonly'):
            raise ValueError('原始资料不可覆盖，请保存为个人副本')
        if identifier and (value.get('attachment') or any(
                str(value.get(key,item.get(key,'')))!=str(item.get(key,''))
                for key in ('title','organization','published_at','source_url','body'))):
            raise ValueError('已保存的原文不可改写，请另存个人副本')
        kind=value.get('source_type',item.get('source_type','report'))
        if kind not in ('report','blog','paper'):raise ValueError('资料类型须为金工研报、博客或论文')
        item['source_type']=kind
        item.update({key:text_field(value.get(key,item.get(key)),label,limit,required) for key,label,limit,required in (
            ('title','标题',180,True),('organization','机构',100,False),('published_at','发布日期',20,False),
            ('body','研报正文',200000,False),('notes','阅读笔记',200000,False))})
        if item['published_at']:
            datetime.strptime(item['published_at'],'%Y-%m-%d')
        item['source_url']=source_url(value.get('source_url',item.get('source_url')))
        attachment=value.get('attachment')
        if attachment:
            filename=Path(str(attachment.get('filename','')).replace('\\','/')).name
            suffix=Path(filename).suffix.lower()
            if suffix not in ('.pdf','.txt','.md'):
                raise ValueError('附件仅支持 PDF、TXT、Markdown')
            try:
                content=base64.b64decode(attachment.get('content_base64',''),validate=True)
            except (ValueError,TypeError):
                raise ValueError('附件编码无效') from None
            if not content or len(content)>10*1024*1024:
                raise ValueError('附件须为非空文件且不超过10MiB')
            if suffix=='.pdf' and not content.startswith(b'%PDF-'):
                raise ValueError('文件不是有效的 PDF 文档')
            if suffix!='.pdf':
                content.decode('utf-8-sig')
            folder=self.root/'attachments';folder.mkdir(exist_ok=True)
            destination=folder/(item['id']+suffix)
            destination.write_bytes(content)
            item.update(attachment_name=filename,attachment_suffix=suffix,
                        attachment_filename=filename,
                        attachment_url='/mgr/research/attachments/'+item['id'])
        return self._save('report',owner,item)

    def save_project(self, owner, value):
        identifier=value.get('id')
        item=self._get('project',owner,identifier) if identifier else {'id':uuid.uuid4().hex,'version':0}
        report=self._get('report',owner,value.get('report_id')) if value.get('report_id') else None
        kind=value.get('kind','timeseries')
        if kind not in ('timeseries','cross_section','spread'):
            raise ValueError('研究类型无效')
        template=value.get('template_id') or None
        if template not in (None,'dual_ma'):
            raise ValueError('模板不存在')
        code=text_field(value.get('code'),'策略代码',1024*1024)
        if not code and template:
            code=template_source()
        elif template and code.replace('\r\n','\n')!=template_source():
            template=None
        if code:
            compile(code,'<research project>','exec')
        item.update(title=text_field(value.get('title'),'项目标题',180,True),report_id=report['id'] if report else None,kind=kind,
                    hypothesis=text_field(value.get('hypothesis'),'研究假设'),
                    formula=text_field(value.get('formula'),'公式'),notes=text_field(value.get('notes'),'项目笔记'),
                    template_id=template,code=code,version=item['version']+1)
        return self._save('project',owner,item)

    def run(self, user, value):
        owner=user['loginid'];project=self._get('project',owner,value.get('project_id'))
        if project['kind']!='timeseries':
            raise ValueError('当前执行入口先支持时序研究；截面与套利可登记项目，暂不启动计算')
        if project.get('template_id')!='dual_ma':
            raise ValueError('首版仅执行内置研究模板；自定义代码作为研究笔记保存，不自动执行')
        dataset=next((row for row in self.datasets(user) if row['id']==value.get('dataset')),None)
        if not dataset:
            raise ValueError('数据集不存在或没有访问权限；双均线模板仅支持 K 线数据')
        start,end=selection_bounds(dataset,value.get('start'),value.get('end'))
        capital=float(value.get('capital',1000000))
        slippage=int(value.get('slippage',1))
        if not math.isfinite(capital) or not 0<capital<=1e9 or not 0<=slippage<=100:
            raise ValueError('资金或滑点参数无效')
        params=template_params(value.get('params'))
        code=template_source(params)
        if not code.strip():
            raise ValueError('请先选择模板或保存策略代码')
        snapshot=dict(project,code=code)
        report=self._get('report',owner,project['report_id']) if project.get('report_id') else dict(title='独立研究项目',organization='',published_at='',source_url='')
        with self.lock:
            if self.closing:
                raise RuntimeError('研究服务正在关闭')
            for old in self._rows('experiment'):
                old=self._sync(old['owner'],old)
                if old['status'] not in TERMINAL:
                    raise RuntimeError('已有研究实验运行中，请完成或取消后再提交')
            item=dict(id=uuid.uuid4().hex,owner=owner,project_id=project['id'],project_version=project['version'],
                      project_title=project['title'],dataset=dataset['id'],code=dataset['code'],period=dataset['period'],
                      start=start,end=end,capital=capital,slippage=slippage,params=params,status='queued',progress=0,
                      native_id=None,strategy_id=None,error='',metrics={},project_snapshot=snapshot,
                      report_snapshot=report,data_snapshot=dataset)
            self._save('experiment',owner,item)
            threading.Thread(target=self._launch,args=(owner,item['id']),name='research-native',daemon=True).start()
        return {key:val for key,val in item.items() if key not in ('project_snapshot','report_snapshot')}

    def _launch(self, owner, identifier):
        with self.lock:
            item=self._get('experiment',owner,identifier)
            if item['status'] in TERMINAL:
                return
            try:
                strategy=self.bt_mon.add_strategy(owner,'研究 · '+item['project_title'][:50]+' · '+identifier[:6],'cta')
                self.bt_mon.set_strategy_code(owner,strategy['id'],item['project_snapshot']['code'])
                item.update(strategy_id=strategy['id'],status='preparing')
                self._save('experiment',owner,item)
                native=self.bt_mon.run_backtest(owner,strategy['id'],item['start'],item['end'],item['capital'],
                    slippage=item['slippage'],fill_model='causal_touch' if item['period']=='tick' else 'legacy_cta',dataset=item['dataset'])
                item.update(native_id=native['id'],status=native['status'])
                self._save('experiment',owner,item)
                task=self.bt_mon.task_map.get(native['id'])
                if task and task._procid and hasattr(os,'setpriority'):
                    try:
                        os.setpriority(os.PRIO_PROCESS,task._procid,10)
                    except (OSError,ProcessLookupError):
                        pass
            except Exception as error:
                item.update(status='failed',error=str(error))
                self._save('experiment',owner,item)

    def _sync(self, owner, item):
        with self.lock:
            return self._sync_locked(owner,item)

    def _sync_locked(self, owner, item):
        item=self._get('experiment',owner,item['id'])
        if item['status'] in TERMINAL:
            return item
        if not item.get('native_id'):
            return item
        native=self.bt_mon.get_backtest_detail(owner,item['strategy_id'],item['native_id'])
        if native is None:
            item.update(status='interrupted',error='原生回测记录不可用，保留实验输入以便重跑')
        else:
            item.update(status=native['status'],progress=native['state'].get('progress',0),error=native.get('error') or '')
            if item['status'] in TERMINAL:
                result=self._result(owner,item,native)
                folder=self.root/'results';folder.mkdir(exist_ok=True)
                (folder/(item['id']+'.json')).write_text(json.dumps(finite(result),ensure_ascii=False,allow_nan=False),encoding='utf-8')
                item['metrics']=result['metrics']
                item['read_url']='/mgr/research/results/'+item['id']
        return self._save('experiment',owner,item)

    def _result(self, owner, item, native):
        straid,btid=item['strategy_id'],item['native_id']
        trades=self.bt_mon.get_bt_trades(owner,straid,btid) or []
        funds=self.bt_mon.get_bt_funds(owner,straid,btid) or []
        signals=self.bt_mon.get_bt_signals(owner,straid,btid) or []
        rounds=self.bt_mon.get_bt_rounds(owner,straid,btid) or []
        summary=native.get('summary') or {}
        metrics=dict(trade_count=len(trades),closed_trades=len(rounds),signal_count=len(signals),
            fee=funds[-1].get('fee') if funds else None,total_return=summary.get('total_return'),
            annual_return=summary.get('annual_return'),max_drawdown=summary.get('max_falldown'),
            sharpe=summary.get('sharpe_ratio'),days=summary.get('days'))
        folder=Path(self.bt_mon.path)/owner/straid/'backtests'/btid
        path=folder/'research-diagnostics.json'
        diagnostic=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if not diagnostic:
            diagnostic={'message':'此自定义策略没有输出信号诊断；可查看原生目标记录、成交与日志。'}
        elif diagnostic.get('calculations',0)==diagnostic.get('warmup_skips',0):
            diagnostic['message']='计算回调均因预热不足跳过，请扩大可用历史或缩短研究周期。'
        else:
            diagnostic['message']='信号统计来自策略回调；成交、费用和收益来自 WT 原生结果。'
        return dict(metrics=metrics,diagnostics=diagnostic,funds=funds,trades=trades[:1000],
                    signals=signals[:1000],logs=native.get('logs',''),summary=summary,
                    data_model=item['data_snapshot'].get('model'),fill_model='causal_touch' if item['period']=='tick' else 'legacy_cta')

    def detail(self, owner, identifier):
        item=self._sync(owner,self._get('experiment',owner,identifier))
        path=self.root/'results'/(item['id']+'.json')
        result=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else dict(metrics={},diagnostics={},trades=[],funds=[])
        visible={key:val for key,val in item.items() if key not in ('project_snapshot','report_snapshot')}
        visible['log_tail']=result.get('logs','')
        return dict(experiment=visible,
                    project=item['project_snapshot'],report=item['report_snapshot'],**result,
                    read_url=item.get('read_url'))

    def cancel(self, owner, identifier):
        with self.lock:
            item=self._sync(owner,self._get('experiment',owner,identifier))
            if item['status'] not in TERMINAL:
                if item.get('native_id'):
                    self.bt_mon.stop_backtest(owner,item['strategy_id'],item['native_id'])
                    item=self._sync(owner,item)
                if item['status']=='completed':
                    return item
                item.update(status='stopped',error='用户取消实验')
                self._save('experiment',owner,item)
            return item

    def attachment(self, owner, identifier):
        report=self._get('report',owner,identifier)
        if not report.get('attachment_suffix'):
            raise ValueError('该研报没有本地附件')
        return self.root/'attachments'/(report['id']+report['attachment_suffix']), report['attachment_name']

    def result_html(self, owner, identifier):
        data=self.detail(owner,identifier)
        exp,project,report=data['experiment'],data['project'],data['report']
        escape=lambda value:html.escape(str(value if value is not None else '—'))
        labels={'trade_count':'成交记录数','closed_trades':'平仓笔数','signal_count':'目标信号数','fee':'累计手续费',
                'total_return':'区间收益率（%）','annual_return':'年化收益率（%）','max_drawdown':'最大回撤（%）',
                'sharpe':'夏普比率','days':'原生资金交易日数'}
        metrics=''.join('<tr><th>'+labels[key]+'</th><td>'+escape(round(val,6) if isinstance(val,float) else val)+'</td></tr>' for key,val in data['metrics'].items())
        diagnostic=data['diagnostics']
        explanation='计算回调 '+escape(diagnostic.get('calculations'))+' 次，其中预热不足跳过 '+escape(diagnostic.get('warmup_skips'))+' 次。'
        explanation+='多头方向 '+escape(diagnostic.get('long_signals'))+' 次，空头方向 '+escape(diagnostic.get('short_signals'))+' 次。'
        explanation+='目标改变 '+escape(diagnostic.get('target_changes'))+' 次，实际成交 '+escape(data['metrics'].get('trade_count'))+' 条。'
        conditions='输入使用闭合K线事件。' if exp['period']!='tick' else '输入使用所选历史Tick。'
        conditions+='滑点为 '+escape(exp['slippage'])+' 跳；手续费取所选数据集的费率快照。'
        funds=''.join('<tr><td>'+escape(row['date'])+'</td><td>'+escape(row['dynbalance'])+'</td><td>'+escape(row['fee'])+'</td></tr>' for row in data['funds'])
        title=escape(project['title'])
        return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>'''+title+'''</title><style>body{max-width:960px;margin:35px auto;padding:0 24px;color:#24364b;font:16px/1.8 system-ui,sans-serif}h1,h2{line-height:1.4}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #dce3eb;padding:10px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:18px}small{color:#596a80}</style>
<h1>'''+title+'''</h1><p>'''+escape(exp['status'])+' · 实验 '+escape(exp['id'])+' · 项目版本 '+escape(exp['project_version'])+'''</p>
<p><strong>本实验是双均线方法验证，不代表整篇研报的模型和收益复刻。</strong></p>
<h2>研究什么？</h2><p>'''+escape(project['hypothesis'])+'''</p><pre>'''+escape(project['formula'])+'''</pre>
<h2>原文在哪里？</h2><p>'''+escape(report['title'])+' · '+escape(report['organization'])+' · '+escape(report['published_at'])+'''</p><p><a rel="noopener noreferrer" href="'''+html.escape(report['source_url'],quote=True)+'''">打开原文</a></p>
<h2>用了哪些数据？</h2><p>'''+escape(exp['code'])+' · '+escape(exp['period'])+' · '+escape(exp['start'])+' 至 '+escape(exp['end'])+'''</p>
<p>'''+conditions+'''</p><h2>WT 实际算出了什么？</h2><table>'''+metrics+'''</table><p>收益率单位为百分比。费用和收益来自原生回测。年化收益和夏普只是所列样本的换算。</p>
<h2>信号经过了哪些检查？</h2><p>'''+explanation+'''</p><p>'''+escape(diagnostic.get('message',''))+'''</p>
<h2>每日资金结果怎样变化？</h2><table><tr><th>交易日</th><th>原生累计净损益</th><th>累计手续费</th></tr>'''+funds+'''</table>
<h2>怎样重跑？</h2><p>在原项目选择同一数据集、日期与模板参数，创建新实验。</p><details><summary>查看实验参数</summary><pre>'''+escape(json.dumps({key:exp[key] for key in ('dataset','start','end','capital','slippage','params')},ensure_ascii=False,indent=2))+'''</pre></details>
<details><summary>查看逐点信号诊断</summary><pre>'''+escape(json.dumps(diagnostic,ensure_ascii=False,indent=2))+'''</pre></details>
<details><summary>查看策略快照</summary><pre>'''+escape(project['code'])+'''</pre></details><p>'''+escape(exp['error'])+'''</p></html>'''

    def shutdown(self):
        self.closing=True
        for item in self._rows('experiment'):
            if item['status'] not in TERMINAL:
                self.cancel(item['owner'],item['id'])
