"""Read-only public projection: no formulas, parameters, paths or model outputs."""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone


def snapshot(root,owner):
    with closing(sqlite3.connect(root/'research.sqlite3',timeout=2)) as db:
        records=db.execute('SELECT kind,data FROM records WHERE owner=?',(owner,)).fetchall()
    groups={}
    for kind,data in records:groups.setdefault(kind,[]).append(json.loads(data))
    def counts(kind):return dict(Counter(row.get('status','unknown') for row in groups.get(kind,[])))
    versions=groups.get('automated_factor_version',[])
    states={r['version_id']:r for r in groups.get('factor_version_state',[])}
    factors=[]
    for i,row in enumerate(sorted(versions,key=lambda r:r['id'])):
        state=states.get(row['id'],{});monitor=state.get('latest_monitor') or {}
        factors.append(dict(label='因子 '+str(i+1),status=row['status'],frequency=row['spec']['frequency'],
            monitor_at=monitor.get('created_at'),color=monitor.get('calibrated_lifecycle',{}).get('color','unobserved'),
            rank_ic=monitor.get('metrics',{}).get('oriented_rank_ic'),
            coverage=monitor.get('coverage'),promotion=state.get('promotion',{}).get('status','not_eligible'),
            retirement=state.get('retirement',{}).get('status','not_requested')))
    cycles=[]
    for row in groups.get('factor_automation_cycle',[]):
        cycles.append(dict(phase=row['id'].removeprefix('factor-automation-'),status=row['status'],checked_at=row.get('checked_at'),
            native=[{key:r.get(key) for key in ('status','completed_days','target_days')} for r in (row.get('native_matrix') or {}).get('results',[])]))
    return dict(result=0,as_of=datetime.now(timezone.utc).isoformat(),read_only=True,
        reports=counts('factor_report_screen'),jobs=counts('factor_automation'),factors=factors,cycles=cycles,
        tools=['RD-Agent','KunQuant','TA-Lib','Alphalens','Optuna','Great Expectations','MLflow','DSR/PBO','fincore'],
        stages=['来源与单点假设','数据时点与成本','隔离计算与相关性去重','全部候选原生回放',
                'DSR/PBO 与账户绩效','独立前向验证','安全应用','衰减保护与空仓暂停'],
        policy=dict(native_days=40,forward_days=20,closed_trades=60),
        note='通过工程检查不等于因子通过准入。没有新监测样本时，不显示正常。')
