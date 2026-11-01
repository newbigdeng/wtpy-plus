import {test} from 'node:test'
import assert from 'node:assert/strict'
import {returnsView,reportTime} from '../src/components/backtest/reportReturns.js'

test('month changes use previous month end and drawdown starts at initial capital',()=>{
  const view=returnsView([['20261030',90],['20261102',99],['20261103',108.9]],100,252)
  assert.equal(view.stats.days,3)
  assert.ok(Math.abs(view.months[0].value+.10)<1e-8)
  assert.ok(Math.abs(view.months[1].value-.21)<1e-8)
  assert.ok(Math.abs(view.stats.drawdown-.10)<1e-8)
})

test('daily table uses last equity and carries it across months',()=>{
  const r=returnsView([['20261030',110],['20261030',90],['20261102',99]],100,252)
  assert.deepEqual(r.days.map(x=>[x.day,x.pnl]),[['20261030',-10],['20261102',9]])
  assert.ok(Math.abs(r.days[1].rate-.10)<1e-8)
  assert.deepEqual(returnsView([],null,252).days,[])
})

test('counter fills use natural date, leading hour zero and millisecond precision',()=>{
  assert.equal(reportTime({date:20261023,time:95000500},'plus'),'2026-10-23 09:50:00.500')
  assert.equal(reportTime({date:20261030,trading_date:20261102,time:210000001},'plus'),'2026-10-30 21:00:00.001')
  assert.equal(reportTime({time:202610230950},'native'),'2026-10-23 09:50')
})
