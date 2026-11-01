import {test} from 'node:test'
import assert from 'node:assert/strict'
import {returnsView} from '../src/components/backtest/reportReturns.js'

test('month changes use previous month end and drawdown starts at initial capital',()=>{
  const view=returnsView([['20261030',90],['20261102',99],['20261103',108.9]],100,252)
  assert.equal(view.stats.days,3)
  assert.ok(Math.abs(view.months[0].value+.10)<1e-8)
  assert.ok(Math.abs(view.months[1].value-.21)<1e-8)
  assert.ok(Math.abs(view.stats.drawdown-.10)<1e-8)
})
