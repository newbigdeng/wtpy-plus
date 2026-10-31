export function cardPercent(result, key) {
  const value = result?.metrics?.[key];
  return Number.isFinite(value) ? value.toFixed(2) + '%' : '—';
}

export function cardCaption(result) {
  if (!result) return '暂无已完成结果';
  const mode = result.mode === 'plus' ? 'Plus' : '原生回测';
  const version = result.matches_saved_code === false ? ' · 历史代码' : result.matches_saved_code == null ? ' · 代码版本未知' : '';
  const date = result.created_at ? ' · ' + new Date(result.created_at * 1000).toLocaleDateString('zh-CN', {timeZone:'Asia/Shanghai'}) : '';
  return mode + date + version;
}
