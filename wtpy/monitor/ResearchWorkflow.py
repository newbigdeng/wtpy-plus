"""Owner-scoped iterations over a frozen factor input, never a live strategy deployer."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid


class ResearchWorkflow:
    def __init__(self,factors):
        self.factors=factors;self.workspace=factors.workspace

    def _analysis_path(self,item):
        return self.workspace.root/'workflow-results'/(item['id']+'.json')

    def _input_path(self,item):
        return self.workspace.root/'workflow-results'/(item['id']+'-input.csv')

    def _compute(self,payload):
        process=subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).with_name('ResearchDiagnostics.py'))],
            input=json.dumps(payload),capture_output=True,text=True,encoding='utf-8',timeout=180)
        try:result=json.loads(process.stdout.splitlines()[-1])
        except (ValueError,IndexError):raise ValueError('研究检查子进程没有返回有效结果') from None
        if process.returncode or result.get('error'):raise ValueError(result.get('error') or '研究检查失败')
        return result

    def run(self,user,value):
        owner=user['loginid'];project=self.workspace._get('project',owner,value.get('project_id'))
        if project.get('kind','timeseries')!='timeseries':raise ValueError('研究检查先支持单合约时序；截面和套利需要各自的数据对齐方式')
        source=self.workspace._get('factor_experiment',owner,value.get('input_experiment_id'))
        if source['project_id']!=project['id'] or source['status']!='completed':raise ValueError('请选择当前项目已完成的因子实验作为固定输入')
        identifiers=value.get('factor_ids') or [source['factor_id']]
        windows=value.get('windows')
        if not isinstance(identifiers,list) or not 1<=len(identifiers)<=3 or len(set(identifiers))!=len(identifiers):raise ValueError('请选择1至3个不同因子')
        definitions=[self.workspace._get('factor',owner,identifier) for identifier in identifiers]
        if windows is None:windows=sorted({factor['lookback'] for factor in definitions})
        if not isinstance(windows,list) or not 1<=len(windows)<=3 or any(isinstance(w,bool) or not isinstance(w,int) or not 2<=w<=500 for w in windows):raise ValueError('候选窗口须为1至3个2到500的整数')
        windows=sorted(set(windows));candidates=[]
        for factor in definitions:
            for window in windows:
                # The same builtin+window is one calculation, even when registered twice.
                if any(row['builtin_id']==factor['builtin_id'] and row['lookback']==window for row in candidates):continue
                candidates.append(dict(id='candidate-'+str(len(candidates)+1),factor_id=factor['id'],factor_version=factor['version'],
                    name=factor['name'],builtin_id=factor['builtin_id'],builtin_version=factor['builtin_version'],lookback=window))
        horizon=source['horizon']
        train=value.get('train_percent',60);validation=value.get('validation_percent',20)
        if any(isinstance(x,bool) or not isinstance(x,int) for x in (train,validation)):raise ValueError('时间比例必须为整数')
        if not 40<=train<=80 or not 10<=validation<=30 or train+validation>90:raise ValueError('请为保留测试留出至少10%的时间样本')
        source_path=self.workspace.root/'factor-results'/(source['id']+'-input.csv')
        if not source_path.is_file():raise ValueError('该实验缺少固定输入CSV，请先重新运行因子实验')
        if not self.factors.lock.acquire(blocking=False):raise ValueError('已有因子或研究检查运行中')
        item=dict(id=uuid.uuid4().hex,owner=owner,project_id=project['id'],project_title=project['title'],
            project_version=project['version'],input_experiment_id=source['id'],dataset=source['dataset'],code=source['code'],period=source['period'],
            start=source['start'],end=source['end'],horizon=horizon,candidates=candidates,factor_snapshots=definitions,
            input_as_of=source.get('input_as_of'),
            train_percent=train,validation_percent=validation,status='comparing',lifecycle='candidate',selected_candidate_id=None,
            test_revealed=False,error='',decision_note='',decisions=[],
            holdout_note='保留测试只在本流程内锁定。来源历史若已被查看，不能称为全新未知样本。')
        try:
            folder=self.workspace.root/'workflow-results';folder.mkdir(exist_ok=True)
            shutil.copyfile(source_path,self._input_path(item))
            self.workspace._save('research_workflow',owner,item)
        except Exception:
            self.factors.lock.release();raise
        def work():
            try:
                analysis=self._compute(dict(action='compare',input_file=str(self._input_path(item)),candidates=candidates,horizon=horizon,
                    train_percent=train,validation_percent=validation,start=source['start'],end=source['end'],
                    period=item['period'],as_of=item['input_as_of']))
                self._analysis_path(item).write_text(json.dumps(analysis,ensure_ascii=False,allow_nan=False),encoding='utf-8')
                item.update(status='review',recommended_candidate_id=analysis['recommended_candidate_id'])
            except Exception as exc:item.update(status='failed',error=str(exc)[:1000])
            finally:self.workspace._save('research_workflow',owner,item);self.factors.lock.release()
        threading.Thread(target=work,name='research-check',daemon=True).start()
        return item

    def detail(self,owner,identifier):
        item=self.workspace._get('research_workflow',owner,identifier);path=self._analysis_path(item)
        return dict(workflow=item,analysis=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None)

    def choose(self,owner,value):
        with self.workspace.lock:
            item=self.workspace._get('research_workflow',owner,value.get('workflow_id'))
            if item['status']!='review' or item['test_revealed'] or item['lifecycle']!='candidate':raise ValueError('只能在保留测试揭示前选择候选')
            if value.get('candidate_id') not in [row['id'] for row in item['candidates']]:raise ValueError('候选不属于该研究检查')
            item.update(selected_candidate_id=value['candidate_id'],selected_at=time.time())
            return self.workspace._save('research_workflow',owner,item)

    def reveal(self,owner,identifier):
        if not self.factors.lock.acquire(blocking=False):raise ValueError('已有因子或研究检查运行中')
        try:
            with self.workspace.lock:
                detail=self.detail(owner,identifier);item=detail['workflow'];analysis=detail['analysis']
                if item['test_revealed']:return detail
                if item['status']!='review' or not item.get('selected_candidate_id') or item['lifecycle']!='candidate':raise ValueError('先比较训练/验证，再选定一个候选')
                candidate=next(row for row in analysis['candidates'] if row['id']==item['selected_candidate_id'])
                item.update(status='testing');self.workspace._save('research_workflow',owner,item)
            try:
                test=self._compute(dict(action='reveal',input_file=str(self._input_path(item)),candidate=candidate,horizon=item['horizon'],
                    split=analysis['split'],period=item['period'],as_of=item.get('input_as_of')))
                analysis.update(test_revealed=True,test_candidate_id=candidate['id'],test=test)
                self._analysis_path(item).write_text(json.dumps(analysis,ensure_ascii=False,allow_nan=False),encoding='utf-8')
                item.update(status='evaluated',test_revealed=True,test_revealed_at=time.time())
                self.workspace._save('research_workflow',owner,item)
            except Exception:
                item.update(status='review');self.workspace._save('research_workflow',owner,item);raise
            return dict(workflow=item,analysis=analysis)
        finally:self.factors.lock.release()

    def decide(self,owner,value):
        with self.workspace.lock:
            item=self.workspace._get('research_workflow',owner,value.get('workflow_id'))
            decision=value.get('lifecycle');note=str(value.get('note') or '').strip()
            if decision not in ('accepted','rejected','retired') or not note or len(note)>5000:raise ValueError('请选择采纳、拒绝或退役，并填写具体原因')
            if item['status'] in ('comparing','testing','failed'):raise ValueError('先完成研究检查，再保存判断')
            if item['lifecycle'] in ('rejected','retired'):raise ValueError('该记录已结束，请建立新的研究检查继续迭代')
            if decision=='accepted' and (not item['test_revealed'] or item['lifecycle']!='candidate'):raise ValueError('采纳前需要查看选定候选的保留测试')
            if decision=='retired' and item['lifecycle']!='accepted':raise ValueError('只有已采纳的研究候选可以退役')
            if item['lifecycle']=='accepted' and decision!='retired':raise ValueError('已采纳记录如需停用，请登记退役原因')
            item.update(lifecycle=decision,decision_note=note)
            item['decisions'].append(dict(lifecycle=decision,note=note,at=time.time(),candidate_id=item.get('selected_candidate_id')))
            return self.workspace._save('research_workflow',owner,item)
