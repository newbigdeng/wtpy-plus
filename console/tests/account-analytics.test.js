import test from 'node:test';
import assert from 'node:assert/strict';
import {calendarCells, dateKey, metricValue, monthChoices, presetDates, segmentPoints, shanghaiDay} from '../src/js/account-analytics.js';

test('presets follow the broker trading day, including night sessions and year boundaries', () => {
  const fridayNight = Date.parse('2026-10-23T21:30:00+08:00');
  assert.equal(shanghaiDay(fridayNight), '20261023');
  assert.deepEqual(presetDates('today', '20261026'), {start_date:'20261026',end_date:'20261026'});
  assert.deepEqual(presetDates('quarter', '20270112'), {start_date:'20261101',end_date:'20270112'});
  assert.deepEqual(presetDates('year', '20270112'), {start_date:'20270101',end_date:'20270112'});
  assert.deepEqual(presetDates('all', '20270112'), {});
});

test('custom dates reject reversed or nonexistent dates rather than broadening the query', () => {
  assert.equal(dateKey('2026-02-30'), '');
  assert.equal(dateKey(null), '');
  assert.deepEqual(presetDates('custom', '20261026', ['20261026','20261001']), {});
  assert.deepEqual(presetDates('custom', '20261026', ['2026-10-01','2026-10-26']), {start_date:'20261001',end_date:'20261026'});
});

test('calendar distinguishes missing receipts, unknown profit, and an actual zero', () => {
  const daily = [{trading_date:20261026,pnl:12},{trading_date:20261027,pnl:null},{trading_date:20261028,pnl:0}];
  const cells = calendarCells('2026-10', daily);
  assert.equal(cells.find(cell => cell.day === 25).row, null);
  assert.equal(metricValue(cells.find(cell => cell.day === 27).row, 'pnl'), null);
  assert.equal(metricValue(cells.find(cell => cell.day === 28).row, 'pnl'), 0);
  assert.equal(cells[0].day, null); // October starts on Thursday; the grid starts on Monday.
  assert.equal(cells.filter(cell => cell.day).length, 31);
});

test('one capital segment is displayed without deriving profit from equity or old undated points', () => {
  const account = {points:[
    {time:1,equity:10000,segment_id:'initial'},
    {time:2,equity:500000,segment_id:'capital-2'},
    {time:3,equity:500010,pnl:null,return_pct:null,segment_id:'capital-2'}
  ]};
  const points = segmentPoints(account, 'capital-2');
  assert.deepEqual(points.map(point => point.equity), [500000,500010]);
  assert.equal(metricValue(points[0], 'pnl'), null);
  assert.equal(metricValue(points[1], 'return_pct'), null);
  assert.equal(metricValue({pnl:'10'}, 'pnl'), null);
});

test('month selection does not invent monthly performance records', () => {
  const daily = [{trading_date:20260831,pnl:2}], monthly = [{month:'2026-08',pnl:2}];
  assert.deepEqual(monthChoices(daily,monthly,'20261026'), ['2026-10','2026-08']);
  assert.deepEqual(monthly, [{month:'2026-08',pnl:2}]);
});
