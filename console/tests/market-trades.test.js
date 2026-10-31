import {test} from 'node:test'
import assert from 'node:assert/strict'
import {tradeAction,tradeTime,tradeBarIndex,tradeMarkers} from '../src/js/market-trades.js'

const code='SHFE.rb.2701',stamp=Date.parse('2026-10-22T21:27:00+08:00')
const bars=[{time:202610222127,date:20261023},{time:202610222128,date:20261023},{time:202610222129,date:20261023}]

test('native epoch milliseconds display in Beijing and use closing minute labels',()=>{
 assert.equal(tradeTime(stamp),'2026-10-22 21:27:00')
 assert.equal(tradeTime('20261022212700'),'2026-10-22 21:27:00')
 assert.equal(tradeTime('2026-10-22 21:27:00'),'2026-10-22 21:27:00')
 assert.equal(tradeBarIndex({time:stamp},bars,'m1'),1)
 assert.equal(tradeBarIndex({time:stamp-1},bars,'m1'),0)
})

test('minute markers respect five-minute intervals and never leap across session gaps',()=>{
 const five=[{time:202610222130},{time:202610222135},{time:202610230905}]
 assert.equal(tradeBarIndex({time:stamp},five,'m5'),0)
 assert.equal(tradeBarIndex({time:Date.parse('2026-10-22T22:00:00+08:00')},five,'m5'),-1)
 assert.equal(tradeBarIndex({time:Date.parse('2026-10-23T00:00:00+08:00')},[{time:202610230001}],'m1'),0)
})

test('night fills on daily charts use the known exchange trading day',()=>{
 const days=[{time:20261022},{time:20261023}]
 assert.equal(tradeBarIndex({time:stamp,trading_date:20261023},days,'d'),1)
 assert.equal(tradeBarIndex({time:stamp},days,'d'),-1)
})

test('actual fills carry Chinese actions and distinct long, short and close markers',()=>{
 const fills=['OL','OS','CNL','CNS'].map((action,index)=>({code,action,time:stamp,price:3300+index,volume:1,channel:'example',tradeid:String(index)}))
 const markers=tradeMarkers([...fills,{...fills[0],code:'SHFE.cu.2701'},{...fills[0],action:'UNKNOWN'}],bars,code,'m1',{up:'red',down:'green'})
 assert.deepEqual(markers.map(point=>point.name),['开多','开空','平多','平空'])
 assert.deepEqual(markers.map(point=>point.symbol),['triangle','triangle','circle','circle'])
 assert.deepEqual(markers.map(point=>point.itemStyle.color),['red','green','red','green'])
 assert.deepEqual(markers.map(point=>point.symbolRotate),[0,180,0,180])
 assert.ok(markers.every(point=>point.label.show&&point.coord[0]===1))
 assert.equal(markers[0].trade.time_label,'2026-10-22 21:27:00')
 assert.equal(tradeAction('CNL'),'平多')
 assert.equal(tradeAction('CS'),'平空')
 assert.equal(tradeAction('UNKNOWN'),'UNKNOWN')
})
