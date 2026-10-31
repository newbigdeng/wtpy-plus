"""Versioned, allowlisted closed-bar factors. Diagnostics are distinct from WT P&L."""
from __future__ import annotations
import csv
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
import uuid
if __package__:
    from .ResearchDiagnostics import rank_correlation,stability
    from .FactorFormula import bar_available_at,bar_segments,current_stamp,evaluate
else:
    from ResearchDiagnostics import rank_correlation,stability
    from FactorFormula import bar_available_at,bar_segments,current_stamp,evaluate

BUILTINS=[
 dict(id='momentum',version=1,name='历史收益动量',formula='close[t] / close[t-lookback] - 1',
      description='过去价格上涨幅度。正值表示回看窗口上涨。',inputs=['close'],parameters={'lookback':20},min_lookback=2,max_lookback=500),
 dict(id='volatility',version=1,name='历史收益波动',formula='stdev(close[i]/close[i-1]-1, i=t-lookback+1..t)',
      description='过去单根收益的样本标准差。不作年化换算。',inputs=['close'],parameters={'lookback':20},min_lookback=2,max_lookback=500),
 dict(id='ma_gap',version=1,name='价格偏离均线',formula='close[t] / mean(close[t-lookback+1:t+1]) - 1',
      description='当前收盘价偏离过去均价的比例。',inputs=['close'],parameters={'lookback':20},min_lookback=2,max_lookback=500),
]


def parameters(value):
    source=next((row for row in BUILTINS if row['id']==value.get('builtin_id')),None)
    if not source:raise ValueError('只执行内置因子，公式笔记不会作为代码运行')
    lookback=value.get('lookback',20)
    if isinstance(lookback,bool) or not isinstance(lookback,int) or not 2<=lookback<=500:raise ValueError('回看窗口须为2至500根K线')
    return source,lookback


def correlation(left,right):
    if len(left)<3:return None
    lm,rm=statistics.mean(left),statistics.mean(right)
    numerator=sum((a-lm)*(b-rm) for a,b in zip(left,right))
    denominator=math.sqrt(sum((a-lm)**2 for a in left)*sum((b-rm)**2 for b in right))
    return numerator/denominator if denominator else None


def calculate(rows, builtin_id, lookback, horizon=1, start=0, end=999999999999, *,
              period=None, window_semantics=None, data_snapshot=None, as_of=None):
    """At t only closes <=t enter x. Evaluation starts at next bar close (t+1)."""
    parameters(dict(builtin_id=builtin_id,lookback=lookback))
    if not isinstance(horizon,int) or isinstance(horizon,bool) or not 1<=horizon<=100:raise ValueError('评价持有期须为1至100根K线')
    if len(rows)>200000:raise ValueError('单次因子实验最多20万根，请缩短区间')
    stamps=[row['time'] for row in rows]
    if stamps!=sorted(set(stamps)):raise ValueError('输入时间须严格递增且无重复')
    closes=[float(row['close']) for row in rows]
    if any(not math.isfinite(x) or x<=0 for x in closes):raise ValueError('收盘价必须为有限正数')
    if period is None and rows and len(str(stamps[0])) in (8,12):
        period='d' if len(str(stamps[0]))==8 else 'm1'
    expressions={'momentum':'close / lag(close,lookback) - 1',
                 'ma_gap':'close / mean(close,lookback) - 1',
                 'volatility':'std(close / lag(close,1) - 1,lookback)'}
    values=evaluate(rows,expressions[builtin_id],lookback,period=period,window_semantics=window_semantics,
                    data_snapshot=data_snapshot,as_of=as_of)
    segments=bar_segments(rows,period,window_semantics,data_snapshot) if period else [0]*len(rows)
    output=[];warmup=0;unlabelled=0
    for t,row in enumerate(rows):
        if not start<=stamps[t]<=end:continue
        if t<lookback or values[t] is None:warmup+=1;continue
        value=values[t]
        available=bar_available_at(row,period,data_snapshot) if period else stamps[t]
        label_end=t+1+horizon
        entry_valid=t+1<len(rows) and stamps[t+1]<=end and segments[t]==segments[t+1]
        target=closes[label_end]/closes[t+1]-1 if label_end<len(rows) and stamps[label_end]<=end and segments[t]==segments[label_end] else None
        if target is None:unlabelled+=1
        output.append(dict(time=stamps[t],available_at=available,value=value,
                           evaluation_entry=stamps[t+1] if entry_valid else None,
                           evaluation_exit=stamps[label_end] if target is not None else None,forward_return=target))
    paired=[row for row in output if row['forward_return'] is not None]
    if not output:raise ValueError('窗口预热后没有可用因子，请扩大历史或减小窗口')
    ordered=sorted(paired,key=lambda row:row['value']);groups=[]
    for index in range(5):
        group=ordered[len(ordered)*index//5:len(ordered)*(index+1)//5]
        groups.append(dict(group=index+1,count=len(group),mean_forward_return=statistics.mean(row['forward_return'] for row in group) if group else None))
    return dict(observations=output,input_observations=rows,diagnostics=dict(input_rows=len(rows),factor_rows=len(output),paired_rows=len(paired),
        warmup_skips=warmup,unlabelled_rows=unlabelled,ic=correlation([x['value'] for x in paired],[x['forward_return'] for x in paired]),
        rank_ic=rank_correlation([x['value'] for x in paired],[x['forward_return'] for x in paired]),
        time_stability=stability(paired),scope='single_contract_timeseries',
        groups=groups,first_available_at=output[0]['available_at'],last_available_at=output[-1]['available_at'],
        timing='因子仅用当前及过去闭合K线；评价从下一根收盘价开始。',
        interpretation='IC是因子与后续收益的样本相关系数。分组收益未扣费用，不是可成交策略收益。'))


class ResearchFactors:
    def __init__(self,workspace):
        self.workspace=workspace;self.lock=threading.Lock()
        from .ResearchWorkflow import ResearchWorkflow
        self.workflow=ResearchWorkflow(self)

    def list(self,owner):
        return dict(factor_builtins=BUILTINS,factors=self.workspace._rows('factor',owner),factor_experiments=self.workspace._rows('factor_experiment',owner),
                    research_workflows=self.workspace._rows('research_workflow',owner))

    def save(self,owner,value):
        builtin,lookback=parameters(value)
        identifier=value.get('id')
        previous=self.workspace._get('factor',owner,identifier) if identifier else {}
        report_id=value.get('report_id') or None
        if report_id:self.workspace._get('report',owner,report_id)
        name=str(value.get('name') or builtin['name']).strip()
        if not name or len(name)>180:raise ValueError('因子名称为空或过长')
        item=dict(id=identifier or uuid.uuid4().hex,name=name,builtin_id=builtin['id'],builtin_version=builtin['version'],
                  version=previous.get('version',0)+1,formula=builtin['formula'],lookback=lookback,
                  report_id=report_id,description=str(value.get('description',''))[:20000],inputs=builtin['inputs'],
                  availability='闭合K线收盘时刻；下一根K线起可用',created_at=previous.get('created_at',time.time()))
        return self.workspace._save('factor',owner,item)

    def run(self,user,value):
        from .HistoryCatalog import dataset_entries,selection_bounds
        from .Roles import group_allowed
        owner=user['loginid'];factor=self.workspace._get('factor',owner,value.get('factor_id'))
        project=self.workspace._get('project',owner,value.get('project_id'))
        catalog=dataset_entries(self.workspace.workspace)
        dataset=next((row for row in catalog if row['id']==value.get('dataset') and row['period'] in ('m1','m5','d') and group_allowed(user,row['groupid'])),None)
        if not dataset:raise ValueError('数据集不存在或没有访问权限')
        start,end=selection_bounds(dataset,value.get('start'),value.get('end'))
        horizon=value.get('horizon',1)
        if not isinstance(horizon,int) or isinstance(horizon,bool) or not 1<=horizon<=100:raise ValueError('评价持有期须为1至100根K线')
        native_id=value.get('native_experiment_id') or None
        if native_id:
            experiment=self.workspace._get('experiment',owner,native_id)
            if experiment['project_id']!=project['id']:raise ValueError('原生实验不属于当前研究项目')
        if not self.lock.acquire(blocking=False):raise ValueError('已有因子实验运行中')
        identifier=uuid.uuid4().hex
        item=dict(id=identifier,owner=owner,project_id=project['id'],project_version=project['version'],
                  factor_id=factor['id'],factor_version=factor['version'],factor_snapshot=factor,project_title=project['title'],
                  dataset=dataset['id'],code=dataset['code'],period=dataset['period'],start=start,end=end,horizon=horizon,
                  native_experiment_id=native_id,status='running',error='',reproduction_level='内置公式在WT闭合历史上的因子诊断')
        self.workspace._save('factor_experiment',owner,item)
        def work():
            try:
                payload=dict(files=dataset['files'],period=dataset['period'],start=0,end=end,builtin_id=factor['builtin_id'],
                             lookback=factor['lookback'],horizon=horizon,evaluation_start=start,evaluation_end=end,
                             as_of=current_stamp())
                process=subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).resolve())],input=json.dumps(payload),capture_output=True,text=True,encoding='utf-8',timeout=120)
                result=json.loads(process.stdout.splitlines()[-1])
                if process.returncode or result.get('error'):raise ValueError(result.get('error') or '因子计算子进程失败')
                folder=self.workspace.root/'factor-results';folder.mkdir(exist_ok=True)
                observations=result.pop('observations')
                inputs=result.pop('input_observations')
                with (folder/(identifier+'-input.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=['time','close']);writer.writeheader();writer.writerows(inputs)
                with (folder/(identifier+'.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=['time','available_at','value','evaluation_entry','evaluation_exit','forward_return']);writer.writeheader();writer.writerows(observations)
                result.update(samples=observations[:200],input_snapshot=dict(dataset=dataset['id'],code=dataset['code'],period=dataset['period'],
                    start=start,end=end,as_of=payload['as_of'],closed_bars_only=True,
                    files=[dict(name=Path(p).name,bytes=Path(p).stat().st_size) for p in dataset['files']]),
                    factor_snapshot=factor)
                (folder/(identifier+'.json')).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')
                item.update(status='completed',input_as_of=payload['as_of'],diagnostics=result['diagnostics'],download_url='/mgr/research/factor_results/'+identifier,
                            input_download_url='/mgr/research/factor_results/'+identifier+'?kind=input')
            except Exception as exc:item.update(status='failed',error=str(exc)[:1000])
            finally:
                self.workspace._save('factor_experiment',owner,item);self.lock.release()
        threading.Thread(target=work,name='research-factor',daemon=True).start()
        return item

    def detail(self,owner,identifier):
        item=self.workspace._get('factor_experiment',owner,identifier)
        path=self.workspace.root/'factor-results'/(item['id']+'.json')
        return dict(factor_experiment=item,analysis=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None)


def worker(payload):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent.parent))
    from wtpy.monitor.HistoryDataWorker import dataset_records
    from wtpy.wrapper.WtDtHelper import WtDataHelper
    cutoff=payload.get('as_of',current_stamp())
    if type(cutoff) is not int or cutoff>current_stamp():raise ValueError('future_asof is not allowed')
    payload=dict(payload,end=min(payload.get('end',cutoff),cutoff))
    rows,stamp=dataset_records(payload,WtDataHelper())
    inputs=[dict(time=stamp(row),close=row.close) for row in rows]
    inputs=[row for row in inputs if bar_available_at(row,payload['period'])<=cutoff]
    return calculate(inputs,payload['builtin_id'],payload['lookback'],payload['horizon'],payload['evaluation_start'],
                     min(payload['evaluation_end'],cutoff),period=payload['period'],as_of=cutoff)


if __name__=='__main__':
    try:print(json.dumps(worker(json.load(sys.stdin)),ensure_ascii=False,allow_nan=False),flush=True)
    except Exception as exc:
        print(json.dumps(dict(error=str(exc)),ensure_ascii=False),flush=True);raise SystemExit(1)
