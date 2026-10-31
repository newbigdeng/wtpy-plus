import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mergeMarketQuote,marketFreshness} from '../src/js/market-freshness.js'

const quote=time=>({action_date:20261027,action_time:time,price:3000})
test('older closed session cannot overwrite a newer open session',()=>{
 const full={quote:quote(103000500),session_open:true,session_state:'open',session_checked_at:2000}
 const fast={quote:quote(101500000),session_open:false,session_state:'closed',session_checked_at:1000}
 const value=mergeMarketQuote(full,fast)
 assert.equal(value.session_open,true)
 assert.equal(value.quote,full.quote)
})
test('session and Tick use their own timestamps',()=>{
 const full={quote:quote(103001000),session_open:false,session_checked_at:1000}
 const fast={quote:quote(103000500),session_open:true,session_checked_at:2000}
 const value=mergeMarketQuote(full,fast)
 assert.equal(value.quote,full.quote)
 assert.equal(value.session_open,true)
 const stamp=Date.parse('2026-10-27T10:30:01.500+08:00')
 assert.equal(marketFreshness(value,stamp).age,0)
})
