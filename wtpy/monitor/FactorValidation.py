"""Chronological stability and a bounded block randomization check for rank IC."""
import math
import random

if __package__:
    from .ResearchDiagnostics import ranks, pearson
else:
    from ResearchDiagnostics import ranks, pearson


def block_test(observations, direction, block_size, trials=499):
    """Shuffle complete label blocks; keep serial order inside each block."""
    paired = [row for row in observations if row.get('forward_return') is not None]
    blocks = len(paired) // block_size
    if blocks < 6:
        return dict(blocks=blocks, block_size=block_size, p_value=None,
                    reason='At least six complete dependence blocks are required')
    paired = paired[:blocks * block_size]
    x = ranks([row['value'] for row in paired])
    y = ranks([row['forward_return'] for row in paired])
    observed = pearson(x, y)
    if observed is None:
        return dict(blocks=blocks, block_size=block_size, p_value=None, reason='Constant ranks')
    chunks = [y[i:i + block_size] for i in range(0, len(y), block_size)]
    rng = random.Random(20261028); exceed = 0
    for _ in range(trials):
        shuffled = rng.sample(chunks, len(chunks))
        score = pearson(x, [value for chunk in shuffled for value in chunk])
        exceed += direction * score >= direction * observed
    return dict(blocks=blocks, block_size=block_size, trials=trials,
                p_value=(exceed + 1) / (trials + 1), oriented_rank_ic=direction * observed,
                method='fixed-seed complete-block permutation; approximate dependence control')


def validation_reasons(stability, significance, direction, alpha, prefix):
    reasons = []
    if sum(row['rank_ic'] is not None and direction * row['rank_ic'] > 0 for row in stability) < 2:
        reasons.append(prefix + '_unstable_across_time')
    p = significance['p_value']
    if p is None or not math.isfinite(p) or p > alpha:
        reasons.append(prefix + '_block_significance_failed')
    return reasons
