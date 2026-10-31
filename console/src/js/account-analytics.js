// Display helpers only. Profit and returns come from the broker analytics API.
export function dateKey(value) {
  const key = String(value ?? '').replaceAll('-', '');
  if (!/^\d{8}$/.test(key)) return '';
  const date = new Date(`${key.slice(0, 4)}-${key.slice(4, 6)}-${key.slice(6)}T00:00:00Z`);
  return Number.isFinite(date.valueOf()) && date.toISOString().slice(0, 10).replaceAll('-', '') === key ? key : '';
}

export function isoDate(value) {
  const key = dateKey(value);
  return key ? `${key.slice(0, 4)}-${key.slice(4, 6)}-${key.slice(6)}` : '—';
}

export function shanghaiDay(clock = Date.now()) {
  return new Date(clock + 8 * 3600000).toISOString().slice(0, 10).replaceAll('-', '');
}

export function presetDates(preset, anchor, custom = []) {
  const end = dateKey(anchor);
  if (!end || preset === 'all') return {};
  const year = Number(end.slice(0, 4)), month = Number(end.slice(4, 6));
  let start;
  if (preset === 'today') start = end;
  if (preset === 'month') start = end.slice(0, 6) + '01';
  if (preset === 'quarter') {
    const first = new Date(Date.UTC(year, month - 3, 1));
    start = first.toISOString().slice(0, 10).replaceAll('-', '');
  }
  if (preset === 'year') start = `${year}0101`;
  if (preset === 'custom') {
    const [from, to] = custom.map(dateKey);
    return from && to && from <= to ? {start_date: from, end_date: to} : {};
  }
  return start ? {start_date: start, end_date: end} : {};
}

export function numberText(value, digits = 2) {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
}

export function metricValue(row, metric) {
  const value = row?.[metric];
  return Number.isFinite(value) ? value : null;
}

export function segmentPoints(account, segmentId) {
  return (account?.points || []).filter(point => point.segment_id === segmentId && Number.isFinite(point.time));
}

export function calendarCells(month, daily) {
  if (!/^\d{4}-\d{2}$/.test(month)) return [];
  const [year, number] = month.split('-').map(Number);
  if (number < 1 || number > 12) return [];
  const first = new Date(Date.UTC(year, number - 1, 1));
  const offset = (first.getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(year, number, 0)).getUTCDate();
  const records = new Map(daily.map(row => [dateKey(row.trading_date), row]));
  return Array.from({length: Math.ceil((offset + days) / 7) * 7}, (_, index) => {
    const day = index - offset + 1;
    if (day < 1 || day > days) return {key: `blank-${index}`, day: null, row: null};
    const key = `${month.replace('-', '')}${String(day).padStart(2, '0')}`;
    return {key, day, row: records.get(key) || null};
  });
}

export function monthChoices(daily, monthly, anchor) {
  const months = new Set(monthly.map(row => row.month).filter(value => /^\d{4}-\d{2}$/.test(value)));
  for (const row of daily) {
    const key = dateKey(row.trading_date);
    if (key) months.add(`${key.slice(0, 4)}-${key.slice(4, 6)}`);
  }
  const current = isoDate(anchor).slice(0, 7);
  if (current !== '—') months.add(current);
  return [...months].sort().reverse();
}
