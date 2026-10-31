"""Single-contract time-series research checks, with exact forward-label purging."""
from __future__ import annotations
import csv
import json
import math
import statistics
import sys


def pearson(left,right):
    if len(left)<3:return None
    lm,rm=statistics.mean(left),statistics.mean(right)
    denominator=math.sqrt(sum((x-lm)**2 for x in left)*sum((y-rm)**2 for y in right))
    return sum((x-lm)*(y-rm) for x,y in zip(left,right))/denominator if denominator else None


def ranks(values):
    ordered=sorted(range(len(values)),key=values.__getitem__);result=[0.0]*len(values);i=0
    while i<len(ordered):
        j=i+1
        while j<len(ordered) and values[ordered[j]]==values[ordered[i]]:j+=1
        rank=(i+j-1)/2+1
        for index in ordered[i:j]:result[index]=rank
        i=j
    return result


def rank_correlation(left,right):
    return pearson(ranks(left),ranks(right))


def summary(observations):
    paired=[row for row in observations if row.get('forward_return') is not None]
    values=[row['value'] for row in paired];targets=[row['forward_return'] for row in paired]
    ordered=sorted(paired,key=lambda row:row['value']);groups=[]
    for i in range(5):
        group=ordered[len(ordered)*i//5:len(ordered)*(i+1)//5]
        groups.append(dict(group=i+1,count=len(group),mean_forward_return=statistics.mean(x['forward_return'] for x in group) if group else None))
    valid=[row for row in groups if row['mean_forward_return'] is not None]
    return dict(rows=len(observations),paired_rows=len(paired),coverage=len(paired)/len(observations) if observations else None,
        ic=pearson(values,targets),rank_ic=rank_correlation(values,targets),groups=groups,
        group_monotonicity=rank_correlation([x['group'] for x in valid],[x['mean_forward_return'] for x in valid]),
        start=paired[0]['time'] if paired else None,end=paired[-1]['time'] if paired else None,
        last_label_exit=max((row['evaluation_exit'] for row in paired),default=None))


def stability(observations):
    return [dict(segment=i+1,**summary(observations[len(observations)*i//3:len(observations)*(i+1)//3])) for i in range(3)]


def purged_partition(observations,start=None,end=None,*,end_inclusive=False):
    def before(value):
        return end is None or (value<=end if end_inclusive else value<end)
    selected=[row for row in observations if (start is None or row['time']>=start) and before(row['time'])]
    retained=[row for row in selected if row.get('forward_return') is not None and
              row.get('evaluation_exit') is not None and before(row['evaluation_exit'])]
    return retained,len(selected)-len(retained)


def compare(rows,candidates,horizon,calculate,train_percent=60,validation_percent=20,threshold=.9,start=0,end=999999999999,*,
            period=None,window_semantics=None,data_snapshot=None,as_of=None):
    """Never score the reserved test while comparing candidates."""
    if not 40<=train_percent<=80 or not 10<=validation_percent<=30 or train_percent+validation_percent>90:
        raise ValueError('训练需40%至80%，验证10%至30%，保留测试至少10%')
    if not 0<threshold<=1:raise ValueError('去冗余阈值须大于0且不超过1')
    if not 1<=len(candidates)<=9 or len(rows)>50000:raise ValueError('最多9个候选、5万根固定输入')
    rows=[row for row in rows if row['time']<=end]
    max_window=max(candidate['lookback'] for candidate in candidates)
    timeline=[row for i,row in enumerate(rows) if i>=max_window and i+1+horizon<len(rows) and start<=row['time']<=end and rows[i+1+horizon]['time']<=end]
    if len(timeline)<50:raise ValueError('共同预热和未来标签处理后至少需要50个时间点')
    validation_start=timeline[len(timeline)*train_percent//100]['time']
    test_start=timeline[len(timeline)*(train_percent+validation_percent)//100]['time']
    first=timeline[0]['time'];results=[];training={}
    context={key:value for key,value in dict(period=period,window_semantics=window_semantics,
             data_snapshot=data_snapshot,as_of=as_of).items() if value is not None}
    for candidate in candidates:
        observations=calculate(rows,candidate['builtin_id'],candidate['lookback'],horizon,first,end,**context)['observations']
        train,purged_train=purged_partition(observations,first,validation_start)
        validation,purged_validation=purged_partition(observations,validation_start,test_start)
        if len(train)<5 or len(validation)<5:raise ValueError('标签跨界剔除后样本不足，请缩短评价持有期或扩大历史')
        train_result,validation_result=summary(train),summary(validation)
        direction=1 if (train_result['rank_ic'] or 0)>=0 else -1
        training[candidate['id']]={row['time']:row['value'] for row in train}
        results.append(dict(**candidate,train=train_result,validation=validation_result,
            validation_stability=stability(validation),purged_train=purged_train,purged_validation=purged_validation,
            train_direction=direction,validation_score=direction*validation_result['rank_ic'] if validation_result['rank_ic'] is not None else None,
            redundant_with=None))
    correlations=[];retained=[]
    for candidate in sorted(results,key=lambda row:abs(row['train']['rank_ic'] or 0),reverse=True):
        for other in retained:
            left,right=training[candidate['id']],training[other['id']]
            times=sorted(set(left)&set(right));rho=rank_correlation([left[t] for t in times],[right[t] for t in times])
            if rho is not None and abs(rho)>=threshold:
                candidate['redundant_with']=other['id'];break
        if not candidate['redundant_with']:retained.append(candidate)
    for i,left in enumerate(results):
        for right in results[i+1:]:
            a,b=training[left['id']],training[right['id']];times=sorted(set(a)&set(b))
            correlations.append(dict(left=left['id'],right=right['id'],rows=len(times),rank_correlation=rank_correlation([a[t] for t in times],[b[t] for t in times])))
    eligible=[row for row in retained if row['validation_score'] is not None]
    selected=max(eligible,key=lambda row:row['validation_score'])['id'] if eligible else None
    return dict(scope='single_contract_timeseries',candidates=results,correlations=correlations,recommended_candidate_id=selected,
        split=dict(train_percent=train_percent,validation_percent=validation_percent,test_percent=100-train_percent-validation_percent,
            start=first,validation_start=validation_start,test_start=test_start,end=end,
            purge_rule='前一区间样本的evaluation_exit必须严格早于下一区间首个因子时刻'),
        redundancy_threshold=threshold,test_revealed=False,
        note='按单合约时间排序的研究诊断。重叠标签的样本相关，未换算独立显著性或交易收益。')


def reveal(rows,candidate,horizon,split,calculate,*,period=None,window_semantics=None,data_snapshot=None,as_of=None):
    rows=[row for row in rows if row['time']<=split['end']]
    context={key:value for key,value in dict(period=period,window_semantics=window_semantics,
             data_snapshot=data_snapshot,as_of=as_of).items() if value is not None}
    observations=calculate(rows,candidate['builtin_id'],candidate['lookback'],horizon,split['test_start'],split['end'],**context)['observations']
    retained,discarded=purged_partition(observations,split['test_start'],split['end'],end_inclusive=True)
    metrics=summary(retained)
    return dict(**metrics,stability=stability(retained),unlabelled_rows=discarded,
                train_direction=candidate['train_direction'],
                oriented_rank_ic=candidate['train_direction']*metrics['rank_ic'] if metrics['rank_ic'] is not None else None)


def read_inputs(path):
    with open(path,encoding='utf-8-sig',newline='') as stream:
        rows=[dict(time=int(row['time']),close=float(row['close'])) for row in csv.DictReader(stream)]
    if len(rows)>50000:raise ValueError('流程检查最多5万根输入，请选择较短的固定实验')
    return rows


if __name__=='__main__':
    from ResearchFactors import calculate
    try:
        payload=json.load(sys.stdin);rows=read_inputs(payload.pop('input_file'));action=payload.pop('action')
        result=compare(rows,calculate=calculate,**payload) if action=='compare' else reveal(rows,calculate=calculate,**payload)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
    except Exception as exc:
        print(json.dumps(dict(error=str(exc)),ensure_ascii=False),flush=True);raise SystemExit(1)
