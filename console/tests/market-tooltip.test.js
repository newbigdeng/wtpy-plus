import {test} from 'node:test'
import assert from 'node:assert/strict'
import {marketTooltip} from '../src/js/market-tooltip.js'

const series=[{name:'K线',type:'candlestick'},{name:'成交量',xAxisIndex:1},{name:'MA5'},
 {name:'DIF',xAxisIndex:2},{name:'DEA',xAxisIndex:2},{name:'MACD柱',xAxisIndex:2},
 {name:'K',xAxisIndex:3},{name:'D',xAxisIndex:3},{name:'J',xAxisIndex:3},{name:'BOLL上轨'}]
const bars=[{time:202610210901,open:3300,close:3310,high:3312,low:3298}]
const rows=series.map((item,index)=>({seriesIndex:index,seriesName:item.name,dataIndex:0,axisValueLabel:'09:01',data:index===0?[3300,3310,3298,3312]:index===1?{value:123}:index===5?{value:-1.25}:10}))

test('linked cursor keeps only candle prices and moving averages over the price grid',()=>{
 const text=marketTooltip(rows,series,bars,0)
 assert.match(text,/K线.*3,300.*3,310.*3,312.*3,298/)
 assert.match(text,/MA5: 10/)
 assert.doesNotMatch(text,/成交量|DIF|DEA|MACD|BOLL|K:|D:|J:/)
})
test('volume, MACD and KDJ each show only their own values',()=>{
 assert.equal(marketTooltip(rows,series,bars,1),'09:01<br/>成交量: 123')
 assert.equal(marketTooltip(rows,series,bars,2),'09:01<br/>DIF: 10<br/>DEA: 10<br/>MACD柱: -1.25')
 assert.equal(marketTooltip(rows,series,bars,3),'09:01<br/>K: 10<br/>D: 10<br/>J: 10')
 assert.equal(marketTooltip(rows,series,bars,-1),'')
})
test('disabled panels can shift KDJ to grid 2 and missing indicators stay empty',()=>{
 const changed=[{name:'K',xAxisIndex:2},{name:'D',xAxisIndex:2},{name:'J',xAxisIndex:2}]
 const values=changed.map((item,index)=>({seriesIndex:index,seriesName:item.name,dataIndex:0,data:index?NaN:null}))
 assert.equal(marketTooltip(values,changed,bars,2),'202610210901<br/>K: —<br/>D: —<br/>J: —')
})

test('native marker colours are kept only for the hovered panel and labels are escaped',()=>{
 const markers=rows.map((row,index)=>({...row,marker:`<span style="background-color:${index===5?'#26a69a':'#bd8a35'}"></span>`}))
 const macd=marketTooltip(markers,series,bars,2)
 assert.equal((macd.match(/<span /g)||[]).length,3)
 assert.match(macd,/<span style="background-color:#26a69a"><\/span>MACD柱/)
 assert.doesNotMatch(macd,/MA5|成交量|K线/)
 const escaped=marketTooltip([{...markers[3],axisValueLabel:'<b>09:01</b>',seriesName:'DIF <fast>'}],series,bars,2)
 assert.match(escaped,/&lt;b&gt;09:01&lt;\/b&gt;/)
 assert.match(escaped,/DIF &lt;fast&gt;/)
 assert.doesNotMatch(escaped,/<b>|<fast>/)
})
