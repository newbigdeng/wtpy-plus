<template><section class="history">
  <header><div><h3>{{symbol}} <small>日 K</small></h3><p>{{source}}<template v-if="latest"> · 截至 {{latest.day}}</template></p></div>
    <div class="actions"><select v-model.number="count" aria-label="显示天数"><option v-for="n in [20,60,120]" :key="n" :value="n">近 {{n}} 根</option></select>
    <el-button size="small" :loading="busy" @click="load">刷新日线</el-button></div></header>
  <p v-if="busy || error || !latest" role="status">{{busy?'正在读取日线…':error || '行情源没有返回此合约的有效日线。'}}</p>
  <template v-else>
    <dl><div v-for="[name,val] in stats" :key="name"><dt>{{name}}</dt><dd>{{val}}</dd></div></dl>
    <TerminalChart class="plot" :plot="plot" label="商品期权日K、均线与成交量"/>
  </template>
</section></template>
<script setup>
import {computed,ref,watch,onBeforeUnmount} from 'vue'
import TerminalChart from '../terminal/TerminalChart.vue'
const props=defineProps({symbol:String})
const bars=ref([]),count=ref(60),source=ref(''),busy=ref(false),error=ref('')
const latest=computed(()=>bars.value.at(-1))
const fmt=n=>Number.isFinite(n)?n.toLocaleString('zh-CN',{maximumFractionDigits:3}):'—'
const stats=computed(()=>{
  const b=latest.value,prev=bars.value.at(-2)
  return [['收盘',fmt(b.close)],['较前收',prev?fmt((b.close/prev.close-1)*100)+'%':'—'],['开盘',fmt(b.open)],['最高',fmt(b.high)],['最低',fmt(b.low)],['成交量',fmt(b.volume)+' 手']]
})
const plot=computed(()=>{
  const all=bars.value,rows=all.slice(-count.value),start=all.length-rows.length
  const series=[{name:'日K',type:'candlestick',data:rows.map(b=>[b.open,b.close,b.low,b.high]),itemStyle:{color:'#c44545',borderColor:'#c44545',color0:'#208268',borderColor0:'#208268'}}]
  for(const n of [5,20])series.push({name:'MA'+n,data:rows.map((_,i)=>i+start+1<n?null:all.slice(i+start+1-n,i+start+1).reduce((sum,b)=>sum+b.close,0)/n)})
  return {labels:rows.map(b=>b.day),series,volumes:rows.map(b=>({value:b.volume,itemStyle:{color:b.close>=b.open?'#c44545':'#208268'}})),
    zoom:[{type:'inside'},{type:'slider',height:16,bottom:2}],
    tooltip:{trigger:'axis',renderMode:'richText',formatter(params){const b=rows[params[0]?.dataIndex];return b?[b.day,'开 '+fmt(b.open)+'   高 '+fmt(b.high),'低 '+fmt(b.low)+'   收 '+fmt(b.close),'成交量 '+fmt(b.volume)+' 手'].join('\n'):''}}}
})
let request
async function load(){
  request?.abort();const ctrl=new AbortController();request=ctrl
  bars.value=[];source.value='';error.value='';busy.value=true
  try{
    const r=await fetch('/mgr/assets/option/daily-bars',{method:'POST',signal:ctrl.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:props.symbol})})
    const data=await r.json();if(ctrl.signal.aborted)return
    if(data.result!==0)throw Error(data.message)
    bars.value=data.bars;source.value=data.source
  }catch(e){if(!ctrl.signal.aborted)error.value=e.message}finally{if(!ctrl.signal.aborted)busy.value=false}
}
watch(()=>props.symbol,load,{immediate:true})
onBeforeUnmount(()=>request?.abort())
</script>
<style scoped>
.history{margin-top:18px;border:1px solid var(--wt-line);border-radius:6px;padding:16px;background:var(--wt-surface)}header,.actions,dl{display:flex;align-items:center;gap:10px;flex-wrap:wrap}header{justify-content:space-between}h3,p,dl,dd{margin:0}small,p,dt{font-size:12px;color:var(--wt-muted)}p{margin-top:5px}dl{gap:24px;margin:15px 0}dd{font-variant-numeric:tabular-nums;font-size:16px;margin-top:4px}.plot{height:365px}select{background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line);padding:5px}
</style>
