"""Host-enforced experiment discipline, adapted to the existing WT signal contract.

Method references: quantskills/skill-factor-mine and
quantskills/skill-quant-strategy-diagnostics. No upstream runner is executed.
"""
import csv
import json
from pathlib import Path

VERSION='wt-research-discipline/v2'
REQUIRED=('op_type','hypothesis','change','expected')


def validate_note(spec, parent=None):
    from FactorFormula import canonical
    note=spec.get('iteration_note') or {}
    if any(not isinstance(note.get(key),str) or len(note[key].strip())<3 for key in REQUIRED):
        raise ValueError('iteration_note requires op_type, hypothesis, change and expected')
    if any(note[key].strip() in ('试试看','优化代码','提高收益','提升','更好') for key in REQUIRED[1:]):
        raise ValueError('State the mechanism, exact change and a falsifiable expectation')
    operation=note['op_type']
    if operation=='add_factor':
        if note.get('parent_job_id'):
            raise ValueError('A revision must identify itself as modify_factor')
        return dict(version=VERSION,operation=operation,changed_groups=['new_formula'])
    if operation!='modify_factor' or parent is None:
        raise ValueError('Only add_factor or a verified single-change modify_factor is supported')
    before=parent['spec']
    if (before['frequency']!=spec['frequency'] or before.get('window_semantics')!=spec.get('window_semantics')
            or before['direction']!=spec['direction'] or before['horizon']!=spec['horizon']):
        raise ValueError('A factor revision cannot also change frequency, direction or target labels')
    groups=[]
    if canonical(before['expression'])!=canonical(spec['expression']):groups.append('formula')
    if (before.get('candidate_lookbacks') or [before['lookback']])!=(spec.get('candidate_lookbacks') or [spec['lookback']]):
        groups.append('lookback')
    if len(groups)!=1:
        raise ValueError('Change exactly one formula or lookback group per revision')
    return dict(version=VERSION,operation=operation,changed_groups=groups,parent_job_id=parent['id'])


def frozen_peers(store,owner,item,snapshot,rows=None):
    """Compare frozen definitions on the new training dates, never test outcomes."""
    peers=[]
    for version in store._rows('automated_factor_version',owner):
        if (version['job_id']==item['id'] or version['data_snapshot']['code']!=snapshot['code']
                or version['spec']['frequency']!=item['spec']['frequency']):continue
        if rows is not None:
            from FactorFormula import evaluate
            from ResearchFactorAutomation import _partition_times
            timeline,end,_=_partition_times(rows,item['spec'],item['policy'])
            training=[row for row in rows if row['time']<end]
            candidate=version['selected_candidate']
            values=evaluate(training,candidate['expression'],candidate['lookback'],
                period=version['spec']['frequency'],
                window_semantics=version['spec'].get('window_semantics'),data_snapshot=snapshot)
            peers.append(dict(version_id=version['id'],
                values=[[row['time'],value] for row,value in zip(training,values)
                        if value is not None and row['time']>=timeline[0]['time']],
                training_end=end,basis='frozen peer formula on current training inputs'))
            continue
        path=store.root/'factor-automation'/version['job_id']/'values.csv'
        end=version['split']['validation_start']
        values=[]
        with path.open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream):
                if row['value'] and row['evaluation_exit'] and int(row['evaluation_exit'])<end:
                    values.append([int(row['time']),float(row['value'])])
        peers.append(dict(version_id=version['id'],values=values,training_end=end))
    return peers


def library_gate(training,peers,threshold,minimum_pairs=20):
    from ResearchDiagnostics import rank_correlation
    checks=[]
    for peer in peers:
        other=dict(peer['values']);stamps=sorted(training.keys() & other.keys())
        rho=rank_correlation([training[t] for t in stamps],[other[t] for t in stamps]) if len(stamps)>=minimum_pairs else None
        status='insufficient_overlap' if rho is None else 'duplicate' if abs(rho)>=threshold else 'distinct'
        checks.append(dict(peer=peer['version_id'],pairs=len(stamps),rho=rho,status=status))
    return dict(status='blocked' if any(row['status']!='distinct' for row in checks) else 'passed',
        checks=checks,threshold=threshold,minimum_pairs=minimum_pairs,
        basis='same contract and frequency; matched training timestamps only')


def record_iteration(store,owner,item,phase,analysis=None,error=None):
    record=dict(id=item['id']+'-attempt-'+str(item['attempts']),job_id=item['id'],attempt=item['attempts'],
        phase=phase,discipline_version=VERSION,note=item['spec'].get('iteration_note'),
        source=item['spec']['source'],policy=item['policy'],automatic_apply_eligible=False)
    if analysis:
        record.update(outcome=analysis['status'],reasons=analysis['reasons'],
            selected=analysis['selected_candidate'],correlations=analysis.get('library_correlations',{}),
            validation=[dict(id=row['id'],metrics=row['validation']) for row in analysis['candidates']])
    if error:record['error']=str(error)[:1000]
    if phase!='started':
        record['created_at']=store._get('factor_iteration',owner,record['id'])['created_at']
    store._save('factor_iteration',owner,record)
    path=store.root/'factor-automation'/item['id']/'iteration.json'
    path.write_text(json.dumps(record,ensure_ascii=False,allow_nan=False),encoding='utf-8')


def model_context(store,owner,policy):
    """Feedback omits reserved-test scores and final acceptance decisions."""
    recent=[]
    for row in store._rows('factor_iteration',owner)[:5]:
        recent.append({key:row[key] for key in ('job_id','note','validation','correlations') if key in row})
    return dict(version=VERSION,required_note_fields=list(REQUIRED),allowed_operations=['add_factor','modify_factor'],
        policy=policy,recent_validation_feedback=recent,
        constraints=['One hypothesis and one definition change per iteration',
            'Use existing closed-bar DSL and owner cost registry; never alter gates',
            'No cross-sectional normalization for a single futures contract',
            'Daily aggregates are unavailable intraday; missing inputs and placeholders are not signals',
            'Expected is a preregistered test criterion, not an invented performance promise'])
