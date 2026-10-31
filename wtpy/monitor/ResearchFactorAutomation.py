"""Source-anchored factor research. No strategy activation, trading, or capital ledger."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

if __package__:
    from .FactorValidation import block_test, validation_reasons
    from .FactorFormula import FIELDS, bar_available_at, bar_segments, check_closed_rows, canonical, evaluate, validate
    from .FuturesResearchData import check_requirements, roundtrip_cost
    from .ResearchDiagnostics import purged_partition, rank_correlation, stability, summary
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from FactorValidation import block_test, validation_reasons
    from FactorFormula import FIELDS, bar_available_at, bar_segments, check_closed_rows, canonical, evaluate, validate
    from FuturesResearchData import check_requirements, roundtrip_cost
    from ResearchDiagnostics import purged_partition, rank_correlation, stability, summary


DEFAULT_POLICY = dict(train_percent=60, validation_percent=20, min_partition_rows=10,
                      min_rank_ic=0.02, min_coverage=0.8, roundtrip_cost_bps=5.0,
                      redundancy_threshold=0.95, monitor_window=60, validation_version=3, significance_alpha=0.05)
JOB_KIND = 'factor_automation'
VERSION_KIND = 'automated_factor_version'
RUNNABLE_STATUSES = ('ready', 'failed', 'waiting_data', 'waiting_new_holdout', 'running')
MEAN_DAILY_RETURN = 'mean(close / lag(close, 1) - 1, lookback)'


def _daily_return_source(spec, body, review):
    """One explicit natural-language notation, not a general semantic formula translator."""
    anchors = review.get('source_anchors') or []
    if not isinstance(anchors, list) or not anchors:
        return False
    verified = []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            return False
        quote = anchor.get('exact_quote'); start = anchor.get('text_start')
        if not isinstance(quote, str) or not quote or type(start) is not int or start < 0 or body[start:start + len(quote)] != quote:
            return False
        verified.append(re.sub(r'\s+', '', quote))
    text = '\n'.join(verified)
    required = ('过去一段时间的平均收益率', '建仓日前一天开始向前推R天',
                '计算这R天的平均收益率', '做多平均收益率为正的合约', '做空平均收益率为负的合约')
    return (all(phrase in text for phrase in required) and spec.get('frequency') == 'd'
            and spec.get('direction') == 1 and canonical(spec['expression']) == canonical(MEAN_DAILY_RETURN))


def screen_reports(reports, seen_report_ids=()):
    """Body-based extraction queue, not a claim that a usable formula exists."""
    seen = set(seen_report_ids); result = []
    for report in reports:
        if report['id'] in seen:
            continue
        body = report.get('body') or report.get('text') or ''
        status = 'waiting_text' if not body or report.get('body_truncated') else (
            'awaiting_extraction' if re.search(r'因子|公式|量化|回看|收益率|动量|均线|\b(?:factor|momentum|backtest|overfitting|lookback|causal|regression|walk.forward|cross.validation)\b', body, re.I) else 'not_selected')
        result.append(dict(report_id=report['id'], status=status, source_url=report.get('source_url'),
                           reason='正文关键词筛选；公式是否明确仍需独立核对'))
    return result


def check_spec(spec, report, verification):
    """The caller supplies a separate host-owned review, never a model self-check in spec."""
    source = spec.get('source') or {}
    if source.get('report_id') != report.get('id') or source.get('url') != report.get('source_url'):
        return 'rejected', ['source_identity_mismatch']
    body = report.get('body') or report.get('text') or ''
    if not body or report.get('body_truncated'):
        return 'waiting', ['full_source_text_required']
    quote = source.get('exact_quote'); offset = source.get('text_start')
    if not isinstance(quote, str) or not quote or type(offset) is not int or offset < 0 or body[offset:offset + len(quote)] != quote:
        return 'rejected', ['source_quote_offset_mismatch']
    if not source.get('section'):
        return 'waiting', ['source_section_required']
    expression = spec.get('expression')
    if not expression:
        return 'waiting', ['explicit_formula_required']
    try:
        definition = validate(expression, spec.get('lookback'))
        windows = spec.get('candidate_lookbacks') or [spec['lookback']]
        if not isinstance(windows, list) or not 1 <= len(windows) <= 3 or len(set(windows)) != len(windows):
            raise ValueError('one to three distinct candidate windows required')
        for window in windows:
            validate(expression, window)
        if spec.get('direction') not in (-1, 1) or type(spec['direction']) is not int:
            raise ValueError('direction must be frozen as +1 or -1')
        if type(spec.get('horizon')) is not int or not 1 <= spec['horizon'] <= 100:
            raise ValueError('horizon must be 1..100')
        if spec.get('frequency') not in ('d', 'm1', 'm5'):
            raise ValueError('frequency must be d, m1 or m5')
        semantics = spec.get('window_semantics', 'trading_bars' if spec['frequency'] == 'd' else 'session_contiguous')
        if semantics not in ('trading_bars', 'session_contiguous') or (spec['frequency'] == 'd' and semantics != 'trading_bars'):
            raise ValueError('unsupported window_semantics for source frequency')
    except (ValueError, SyntaxError, TypeError) as exc:
        return 'rejected', ['unsupported_formula: ' + str(exc)]
    inputs = spec.get('inputs') or []
    if (not isinstance(inputs, list) or any(not isinstance(item, dict) for item in inputs)
            or {item.get('field') for item in inputs} != set(definition['fields'])):
        return 'waiting', ['exact_input_requirements_required']
    if any(item.get('as_of') != 'bar_close' for item in inputs):
        return 'waiting', ['input_availability_unverified']
    review = verification or {}
    if (review.get('formula_matches_source') is not True or review.get('input_timing_verified') is not True
            or not review.get('verified_by') or not review.get('reason')):
        return 'waiting', ['independent_formula_review_required']
    if review.get('frequency') != spec['frequency']:
        return 'waiting', ['source_frequency_unverified']
    if (spec['frequency'] != 'd' and semantics == 'trading_bars'
            and review.get('window_semantics') != semantics):
        return 'waiting', ['source_window_semantics_unverified']
    if review.get('window_semantics', semantics) != semantics:
        return 'waiting', ['source_window_semantics_mismatch']
    if spec.get('notation_id') == 'mean_daily_return':
        return ('ready', []) if _daily_return_source(spec, body, review) else (
            'waiting', ['source_formula_not_mechanically_verified'])
    if spec.get('notation_id', 'dsl_literal') != 'dsl_literal':
        return 'waiting', ['unsupported_source_notation']
    literal = review.get('exact_source_formula')
    if not isinstance(literal, str) or literal not in quote:
        return 'waiting', ['source_formula_not_mechanically_verified']
    try:
        # Only source formulas in the same explicit, auditable notation are automatic.
        if canonical(literal) != canonical(expression):
            return 'waiting', ['source_formula_not_mechanically_verified']
    except (SyntaxError, ValueError):
        return 'waiting', ['source_formula_not_mechanically_verified']
    return 'ready', []


def _observations(rows, spec, window, data_snapshot=None, evaluator=evaluate):
    period = spec['frequency']; semantics = spec.get('window_semantics')
    values = evaluator(rows, spec['expression'], window, period=period,
                      window_semantics=semantics, data_snapshot=data_snapshot)
    segments = bar_segments(rows, period, semantics, data_snapshot)
    observations = []; horizon = spec['horizon']
    for index, value in enumerate(values):
        if value is None:
            continue
        end = index + 1 + horizon
        entry_valid = index + 1 < len(rows) and segments[index] == segments[index + 1]
        label_valid = end < len(rows) and segments[index] == segments[end]
        observations.append(dict(time=rows[index]['time'], available_at=bar_available_at(rows[index], period, data_snapshot), value=value,
            evaluation_entry=rows[index + 1]['time'] if entry_valid else None,
            evaluation_exit=rows[end]['time'] if label_valid else None,
            forward_return=rows[end]['close'] / rows[index + 1]['close'] - 1 if label_valid else None,
            diagnostic_cost=roundtrip_cost(rows[index + 1]['close'], rows[end]['close'], data_snapshot or {}) if label_valid else None))
    return observations


def _metrics(observations, direction, cost_bps):
    result = summary(observations)
    paired = [row for row in observations if row.get('forward_return') is not None]
    gross = [(1 if row['value'] > 0 else -1 if row['value'] < 0 else 0) * direction * row['forward_return'] for row in paired]
    costs = [row['diagnostic_cost'] if row.get('diagnostic_cost') is not None else cost_bps / 10000 for row in paired]
    net = [value - (cost if row['value'] != 0 else 0) for value, row, cost in zip(gross, paired, costs)]
    result.update(oriented_rank_ic=direction * result['rank_ic'] if result['rank_ic'] is not None else None,
        mean_directional_return=sum(gross) / len(gross) if gross else None,
        mean_net_directional_return=sum(net) / len(net) if net else None,
        roundtrip_cost_bps=sum(costs) / len(costs) * 10000 if costs else cost_bps,
        cost_source='frozen_contract_model' if any(row.get('diagnostic_cost') is not None for row in paired) else 'legacy_fixed_bps',
        interpretation='重叠前瞻标签的方向收益诊断；每个非零标签扣一笔往返成本，不是WT成交或账户收益。')
    return result


def _partition_times(rows, spec, policy):
    if type(spec.get('horizon')) is not int or not 1 <= spec['horizon'] <= 100:
        raise ValueError('horizon must be 1..100')
    windows = spec.get('candidate_lookbacks') or [spec['lookback']]
    warmup = max(validate(spec['expression'], window)['required_history'] for window in windows)
    timeline = rows[warmup:len(rows) - 1 - spec['horizon']]
    if len(timeline) < policy['min_partition_rows'] * 3:
        raise ValueError('insufficient closed history for fixed three-way evaluation')
    validation_index = len(timeline) * policy['train_percent'] // 100
    test_index = len(timeline) * (policy['train_percent'] + policy['validation_percent']) // 100
    purge = spec['horizon'] + 1
    if min(validation_index - purge, test_index - validation_index - purge, len(timeline) - test_index) < policy['min_partition_rows']:
        raise ValueError('insufficient closed history after exact label-end purging')
    return timeline, timeline[validation_index]['time'], timeline[test_index]['time']


def analyze(rows, spec, policy, data_snapshot=None, evaluator=evaluate, search=None, peer_signals=None):
    """Compare training/validation only, fix the choice, then reveal one test."""
    windows = spec.get('candidate_lookbacks') or [spec['lookback']]
    timeline, validation_start, test_start = _partition_times(rows, spec, policy)
    first = timeline[0]['time']; direction = spec['direction']; candidates = []; training = {}
    computed = {}
    def evaluate_window(window):
        existing = next((candidate for candidate in candidates if candidate['lookback'] == window), None)
        if existing is not None:
            return existing
        observations = _observations(rows, spec, window, data_snapshot, evaluator)
        computed[window] = observations
        train, train_purge = purged_partition(observations, first, validation_start)
        validation, validation_purge = purged_partition(observations, validation_start, test_start)
        if min(len(train), len(validation)) < policy['min_partition_rows']:
            raise ValueError('insufficient rows after exact label-end purging')
        identifier = 'window-' + str(window)
        training[identifier] = {row['time']: row['value'] for row in train}
        candidates.append(dict(id=identifier, expression=spec['expression'], lookback=window,
            horizon=spec['horizon'], direction=direction,
            train=_metrics(train, direction, policy['roundtrip_cost_bps']),
            validation=_metrics(validation, direction, policy['roundtrip_cost_bps']),
            validation_stability=stability(validation), purged_train=train_purge,
            purged_validation=validation_purge))
        return candidates[-1]
    if search is None:
        for window in windows:
            evaluate_window(window)
    else:
        search(windows, evaluate_window)
    correlations = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            a, b = training[left['id']], training[right['id']]; stamps = sorted(set(a) & set(b))
            rho = rank_correlation([a[t] for t in stamps], [b[t] for t in stamps])
            correlations.append(dict(left=left['id'], right=right['id'], rank_correlation=rho,
                                     redundant=rho is not None and abs(rho) >= policy['redundancy_threshold']))
    # Remove equivalent training signals before any validation selection.
    retained = []
    for candidate in sorted(candidates, key=lambda row: (-(row['train']['oriented_rank_ic'] or -2), row['lookback'])):
        candidate['redundant_with'] = next((peer['id'] for peer in retained if any(
            pair['redundant'] and {pair['left'], pair['right']} == {candidate['id'], peer['id']}
            for pair in correlations)), None)
        if candidate['redundant_with'] is None:
            retained.append(candidate)
    library_checks={}
    if policy.get('discipline_version'):
        from ResearchDiscipline import library_gate
        library_checks={row['id']:library_gate(training[row['id']],peer_signals or [],policy['redundancy_threshold'])
                        for row in candidates}
        retained=[row for row in retained if library_checks[row['id']]['status']=='passed']
    library_blocked=not retained
    selected = max(retained, key=lambda row: (row['validation']['oriented_rank_ic']
                   if row['validation']['oriented_rank_ic'] is not None else -2,
                   row['validation']['mean_net_directional_return'] or 0, -row['lookback'])) if retained else min(candidates,key=lambda row:row['lookback'])
    # The single selection is fixed before this first access to reserved-test metrics.
    selected_observations = computed[selected['lookback']]
    test_rows, test_purge = purged_partition(selected_observations, test_start, rows[-1]['time'], end_inclusive=True)
    if library_blocked:test_rows=[]
    test = _metrics(test_rows, direction, policy['roundtrip_cost_bps'])
    eligible = [row for row in rows if first <= row['time'] <= timeline[-1]['time']]
    observed = [row for row in selected_observations if first <= row['time'] <= timeline[-1]['time']]
    coverage = len(observed) / len(eligible)
    reasons = ['library_signal_duplicate_or_uncomparable'] if library_blocked else []
    # Intraday IC has a different scale; significance and chronological
    # stability decide whether a positive relationship is credible.
    ic_floor = 0 if spec['frequency'] in ('m1','m5') else policy['min_rank_ic']
    for name, metrics in (('validation', selected['validation']), ('test', test)):
        if metrics['paired_rows'] < policy['min_partition_rows']:
            reasons.append(name + '_insufficient_labels')
        if (metrics['oriented_rank_ic'] is None or metrics['oriented_rank_ic'] <= 0
                or metrics['oriented_rank_ic'] < ic_floor):
            reasons.append(name + '_weak_or_reversed_rank_ic')
        # Overlapping labels charge one round trip per bar. Actual strategy
        # profitability is decided by native position changes and their costs.
    significance = {}
    if policy.get('validation_version', 1) >= 2:
        validation_rows = purged_partition(selected_observations, validation_start, test_start)[0]
        # A rolling factor and overlapping labels both create dependence.
        block_size = max(spec['horizon'] + 1, validate(spec['expression'], selected['lookback'])['required_history'] + 1)
        alpha = policy['significance_alpha'] / len(windows)
        for name, partition in (('validation', validation_rows), ('test', test_rows)):
            significance[name] = block_test(partition, direction, block_size)
            reasons.extend(validation_reasons(stability(partition), significance[name], direction, alpha, name))
        significance['familywise_alpha'] = policy['significance_alpha']
        significance['candidate_count'] = len(windows)
        significance['adjusted_alpha'] = alpha
    if coverage < policy['min_coverage']:
        reasons.append('insufficient_factor_coverage')
    return dict(status='research_rejected' if reasons else 'research_accepted', reasons=reasons,
        validation_version=policy.get('validation_version', 1), significance=significance,
        candidates=candidates, selected_candidate={key: selected[key] for key in ('id', 'expression', 'lookback', 'horizon', 'direction')},
        selection_basis='validation_only; fixed direction/horizon/cost; one reserved test', test=test,
        test_stability=stability(test_rows), purged_test=test_purge, coverage=coverage, correlations=correlations,
        split=dict(start=first, validation_start=validation_start, test_start=test_start, end=rows[-1]['time'],
                   purge_rule='evaluation_exit strictly earlier than next partition factor time'),
        selection_end_at=max((row['evaluation_exit'] for row in purged_partition(
            selected_observations, validation_start, test_start)[0]), default=validation_start),
        scope='single_contract_timeseries', automatic_apply_eligible=False,
        profitability_gate='native net returns with actual position turnover; label-cost metrics are diagnostic',
        live_gate='研究结果不替代20个前向交易日、60笔完整交易和原生WT/安全应用门槛',
        observations=selected_observations,library_correlations=library_checks,
        reserved_test_evaluated=not library_blocked)


class ResearchFactorAutomation:
    """Owner-scoped SQLite metadata and frozen workspace files, using existing store methods."""

    def __init__(self, workspace, policy=None, runner=None):
        self.workspace = workspace; self.policy = dict(DEFAULT_POLICY, **(policy or {}))
        self.lock = threading.RLock(); self.runner = runner or self._compute
        p = self.policy
        if (not 40 <= p['train_percent'] <= 80 or not 10 <= p['validation_percent'] <= 30
                or p['train_percent'] + p['validation_percent'] > 90 or p['min_partition_rows'] < 5
                or not 0 <= p['roundtrip_cost_bps'] <= 1000 or not 0 <= p['min_rank_ic'] <= 1
                or not 0 < p['min_coverage'] <= 1 or not 0 < p['redundancy_threshold'] <= 1
                or not 10 <= p['monitor_window'] <= 5000):
            raise ValueError('invalid frozen research policy')
        self.root = workspace.root / 'factor-automation'
        self.root.mkdir(exist_ok=True)

    def list(self, owner):
        return dict(jobs=self.workspace._rows(JOB_KIND, owner),
                    versions=self.workspace._rows(VERSION_KIND, owner), monitors=self.workspace._rows('factor_monitor', owner))

    def submit(self, owner, spec, report, verification=None):
        """Report must be the full authorized record from workspace.reports.get(), not list preview."""
        spec = json.loads(json.dumps(spec, allow_nan=False))
        spec.setdefault('window_semantics', 'trading_bars' if spec.get('frequency') == 'd' else 'session_contiguous')
        if spec.get('schema') != 1 or not isinstance(spec.get('extraction_id'), str) or not spec['extraction_id'].strip():
            raise ValueError('schema=1 and stable extraction_id required')
        if spec.get('project_id'):
            self.workspace._get('project', owner, spec['project_id'])
        with self.lock:
            previous = next((row for row in self.workspace._rows(JOB_KIND, owner)
                if row['spec'].get('extraction_id') == spec['extraction_id']
                and row['spec'].get('source', {}).get('report_id') == spec.get('source', {}).get('report_id')), None)
            if previous:
                if previous['spec'] != spec:
                    raise ValueError('extraction_id is immutable; register changed definitions separately')
                if previous['status'] != 'waiting':
                    return previous
            status, reasons = check_spec(spec, report, verification)
            if status=='ready' and self.policy.get('discipline_version'):
                from ResearchDiscipline import validate_note
                parent_id=(spec.get('iteration_note') or {}).get('parent_job_id')
                parent=self.workspace._get(JOB_KIND,owner,parent_id) if parent_id else None
                validate_note(spec,parent)
            item = previous or dict(id=uuid.uuid4().hex, owner=owner, spec=spec, attempts=0)
            refs = [spec.get('source', {})]
            if status == 'ready' and spec.get('notation_id') == 'mean_daily_return':
                refs.extend(dict(anchor, report_id=report['id'], url=report['source_url'])
                            for anchor in verification['source_anchors'])
            item.update(status=status, reasons=reasons, verification=verification, policy=self.policy,
                        source_refs=refs, automatic_apply_eligible=False)
            return self.workspace._save(JOB_KIND, owner, item)

    def _folder(self, item):
        folder = self.root / item['id']; folder.mkdir(exist_ok=True)
        return folder

    def _compute(self, payload):
        process = subprocess.run([sys.executable, '-X', 'utf8', '-I', str(Path(__file__).resolve())],
            input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8', timeout=120)
        try:
            result = json.loads(process.stdout)
        except ValueError:
            raise RuntimeError('formula subprocess returned no JSON result') from None
        if process.returncode or result.get('error'):
            raise RuntimeError(result.get('error') or 'formula subprocess failed')
        return result

    def run(self, owner, identifier, rows, data_snapshot):
        with self.lock:
            item = self.workspace._get(JOB_KIND, owner, identifier)
            if item.get('version_id'):
                return self.detail(owner, identifier)
            if item['status'] not in RUNNABLE_STATUSES:
                return self.detail(owner, identifier)
            fields = set(validate(item['spec']['expression'], item['spec']['lookback'])['fields']) | {'close'}
            if not rows or any(any(field not in row for field in fields) for row in rows):
                item.update(status='waiting_data', reasons=['missing_required_bar_inputs'])
                self.workspace._save(JOB_KIND, owner, item)
                return self.detail(owner, identifier)
            if (data_snapshot.get('closed_bars_only') is not True or not data_snapshot.get('dataset_id')
                    or not data_snapshot.get('code')
                    or data_snapshot.get('period') != item['spec']['frequency']
                    or type(data_snapshot.get('as_of')) is not int):
                item.update(status='waiting_data', reasons=['closed_bar_snapshot_required'])
                self.workspace._save(JOB_KIND, owner, item)
                return self.detail(owner, identifier)
            if item['policy'].get('discipline_version'):
                fields |= {field for field in FIELDS if all(field in row for row in rows)}
            columns = ['time'] + sorted(fields)
            frozen = [{key: row[key] for key in columns} for row in rows]
            # Validate time, values and size before admitting a frozen dataset.
            try:
                check_requirements(item['spec'], data_snapshot)
                check_closed_rows(rows, item['spec']['frequency'], data_snapshot)
                evaluate(frozen, item['spec']['expression'], item['spec']['lookback'], period=item['spec']['frequency'],
                         window_semantics=item['spec'].get('window_semantics'), data_snapshot=data_snapshot)
                if any(type(row['close']) not in (int, float) or not math.isfinite(row['close']) or row['close'] <= 0 for row in frozen):
                    raise ValueError('label close must be finite and positive')
                _partition_times(frozen, item['spec'], item['policy'])
            except ValueError as exc:
                item.update(status='waiting_data', reasons=['input_history_or_values_unavailable'], error=str(exc))
                self.workspace._save(JOB_KIND, owner, item)
                return self.detail(owner, identifier)
            # A renamed formula cannot reuse a revealed holdout from the same report family.
            if item['policy'].get('validation_version', 1) >= 2:
                _, _, test_start = _partition_times(frozen, item['spec'], item['policy'])
                family = dict(source_url=item['spec']['source']['url'], code=data_snapshot['code'],
                              frequency=item['spec']['frequency'])
                reservations = self.workspace._rows('factor_test_reservation', owner)
                revealed = [version for version in self.workspace._rows(VERSION_KIND, owner)
                    if version['job_id'] != item['id']
                    and version['spec']['source']['url'] == family['source_url']
                    and version['data_snapshot']['code'] == family['code']
                    and version['spec']['frequency'] == family['frequency']]
                if any(row['job_id'] != item['id'] and row['family'] == family and test_start <= row['end']
                       for row in reservations) or any(test_start <= row['split']['end'] for row in revealed):
                    item.update(status='waiting_new_holdout', reasons=['This report family already revealed an overlapping test interval'])
                    self.workspace._save(JOB_KIND, owner, item)
                    return self.detail(owner, identifier)
                reservation = dict(id='test-reservation-' + item['id'], job_id=item['id'],
                                   family=family, start=test_start, end=rows[-1]['time'])
                existing = next((row for row in reservations if row['job_id'] == item['id']), None)
                if existing and any(existing[key] != reservation[key] for key in reservation):
                    raise ValueError('Reserved test interval is immutable across retries')
                if not existing:
                    self.workspace._save('factor_test_reservation', owner, reservation)
            folder = self._folder(item); snapshot_path = folder / 'frozen.json'
            frozen_payload = dict(rows=frozen, data_snapshot=data_snapshot, spec=item['spec'], policy=item['policy'])
            if item['policy'].get('discipline_version'):
                from ResearchDiscipline import frozen_peers
                frozen_payload['peer_signals']=(json.loads(snapshot_path.read_text(encoding='utf-8'))['peer_signals']
                    if snapshot_path.exists() else frozen_peers(self.workspace,owner,item,data_snapshot,rows=frozen))
            if snapshot_path.exists():
                if json.loads(snapshot_path.read_text(encoding='utf-8')) != frozen_payload:
                    raise ValueError('retry must reuse the identical frozen input, definition and policy')
            else:
                snapshot_path.write_text(json.dumps(frozen_payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
                with (folder / 'input.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader(); writer.writerows(frozen)
            item.update(status='running', attempts=item['attempts'] + 1, reasons=[])
            item.pop('error', None)
            self.workspace._save(JOB_KIND, owner, item)
            if item['policy'].get('discipline_version'):
                from ResearchDiscipline import record_iteration
                record_iteration(self.workspace,owner,item,'started')
            try:
                analysis = self.runner(dict(input_file=str(snapshot_path)))
                observations = analysis.pop('observations')
                (folder / 'analysis.json').write_text(json.dumps(analysis, ensure_ascii=False, allow_nan=False), encoding='utf-8')
                with (folder / 'values.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=['time', 'available_at', 'value', 'evaluation_entry', 'evaluation_exit', 'forward_return', 'diagnostic_cost'])
                    writer.writeheader(); writer.writerows(observations)
                version = dict(id=item['id'] + '-v1', job_id=item['id'], factor_id=item['id'], version=1,
                    name=item['spec'].get('name', '来源公式因子'), spec=item['spec'], source_refs=item['source_refs'],
                    verification=item['verification'], policy=item['policy'],
                    selected_candidate=analysis['selected_candidate'], status=analysis['status'],
                    data_snapshot=data_snapshot, split=analysis['split'], selection_end_at=analysis['selection_end_at'],
                    diagnostics=analysis, lifecycle='research_only', automatic_apply_eligible=False,
                    artifacts=dict(input='factor-automation/' + item['id'] + '/input.csv',
                                   values='factor-automation/' + item['id'] + '/values.csv',
                                   analysis='factor-automation/' + item['id'] + '/analysis.json'))
                self.workspace._save(VERSION_KIND, owner, version)
                item.update(status=analysis['status'], version_id=version['id'], reasons=analysis['reasons'])
                if item['policy'].get('discipline_version'):
                    record_iteration(self.workspace,owner,item,'completed',analysis=analysis)
            except Exception as exc:
                item.update(status='failed', reasons=['isolated_reproduction_failed'], error=str(exc)[:1000])
                if item['policy'].get('discipline_version'):
                    record_iteration(self.workspace,owner,item,'failed',error=exc)
            self.workspace._save(JOB_KIND, owner, item)
            return self.detail(owner, identifier)

    def version(self, owner, version_id):
        return self.workspace._get(VERSION_KIND, owner, version_id)

    def retry(self, owner, identifier):
        """Resume a failed/interrupted attempt without asking the feeder for a new snapshot."""
        with self.lock:
            item = self.workspace._get(JOB_KIND, owner, identifier)
            path = self.root / item['id'] / 'frozen.json'
            if not path.exists():
                return self.detail(owner, identifier)
            frozen = json.loads(path.read_text(encoding='utf-8'))
            return self.run(owner, identifier, frozen['rows'], frozen['data_snapshot'])

    def detail(self, owner, identifier):
        item = self.workspace._get(JOB_KIND, owner, identifier)
        version = self.version(owner, item['version_id']) if item.get('version_id') else None
        return dict(job=item, version=version)

    def monitor(self, owner, version_id, rows, data_snapshot, exposure_manifest=None, peers=None):
        """Monitor an exact definition; active exposure is never inferred from a research pass."""
        with self.lock:
            version = self.version(owner, version_id); spec = dict(version['spec'])
            candidate = version['selected_candidate']; spec['lookback'] = candidate['lookback']
            if (data_snapshot.get('closed_bars_only') is not True or not data_snapshot.get('dataset_id')
                    or data_snapshot.get('code') != version['data_snapshot']['code']
                    or data_snapshot.get('period') != spec['frequency']
                    or not rows or type(data_snapshot.get('as_of')) is not int):
                raise ValueError('monitor requires a fresh, closed-bar snapshot')
            observations = _observations(rows, spec, candidate['lookback'], data_snapshot)
            paired = [row for row in observations if row['forward_return'] is not None]
            if not paired:
                raise ValueError('monitor has no completed forward labels')
            timeline = rows[:len(rows) - 1 - spec['horizon']][-self.policy['monitor_window']:]
            retained = [row for row in paired if row['time'] >= timeline[0]['time']]
            if timeline[0]['time'] <= version['split']['end']:
                raise ValueError('monitor needs a full new rolling window after the frozen research input')
            metrics = _metrics(retained, candidate['direction'], version['diagnostics']['test']['roundtrip_cost_bps'])
            coverage = len(retained) / len(timeline)
            reasons = []
            if len(retained) < self.policy['monitor_window']:
                reasons.append('insufficient_monitor_labels')
            if metrics['oriented_rank_ic'] is None or metrics['oriented_rank_ic'] < self.policy['min_rank_ic']:
                reasons.append('rolling_ic_weak_or_reversed')
            # Keep label-cost diagnostics in metrics; they are not account PnL.
            if coverage < self.policy['min_coverage']:
                reasons.append('rolling_low_coverage')
            correlations = []
            # Peer series supplied by host must be the same timestamps; no market-wide inference.
            for peer_id, series in (peers or {}).items():
                aligned = [(row['value'], series[row['time']]) for row in retained if row['time'] in series
                           and type(series[row['time']]) in (int, float) and math.isfinite(series[row['time']])]
                rho = rank_correlation([x[0] for x in aligned], [x[1] for x in aligned])
                correlations.append(dict(version_id=peer_id, rows=len(aligned), rank_correlation=rho))
                if rho is not None and abs(rho) >= self.policy['redundancy_threshold']:
                    reasons.append('internal_factor_redundancy')
            active = bool(exposure_manifest and exposure_manifest.get('version_id') == version_id
                          and exposure_manifest.get('active') is True and exposure_manifest.get('applied_revision'))
            result = dict(id=uuid.uuid4().hex, version_id=version_id, job_id=version['job_id'],
                data_snapshot=data_snapshot, metrics=metrics, coverage=coverage, correlations=correlations,
                stability=stability(retained),
                frequency=spec['frequency'], rolling_window_bars=self.policy['monitor_window'],
                scope='single_contract_timeseries', exposure='applied_manifest' if active else 'research_only',
                exposure_manifest=exposure_manifest, reasons=reasons,
                lifecycle_request='deactivation_review' if active and reasons and len(retained) == self.policy['monitor_window']
                                  else 'observe' if reasons else 'continue_observation',
                automatic_apply_eligible=False, note='内部因子相关性不代表全市场拥挤；停用请求仍须安全应用gate。')
            return self.workspace._save('factor_monitor', owner, result)


if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        frozen = json.loads(Path(payload['input_file']).read_text(encoding='utf-8'))
        print(json.dumps(analyze(frozen['rows'], frozen['spec'], frozen['policy'], frozen['data_snapshot'],
                                peer_signals=frozen.get('peer_signals')), ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=False), flush=True)
        raise SystemExit(1)
