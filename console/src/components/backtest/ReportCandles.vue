<template><section class="fill-chart"><h2>成交 K 线</h2>
  <div class="controls"><label v-if="codes.length">合约 <select aria-label="合约" v-model="code" @change="load(code,'')"><option v-for="c in codes" :key="c">{{c}}</option></select></label>
    <label v-if="view?.days.length">自然日 <select aria-label="自然日" v-model="day" @change="load(code,day)"><option v-for="d in view.days" :key="d">{{d}}</option></select></label></div>
  <p v-if="busy || error" role="status">{{error || '读取任务行情和成交…'}}</p>
  <template v-if="view && !busy && !error">
    <p>{{view.source}} <span v-if="view.bars.length">· {{periodName}} · 在图上定位 {{marks.length}} / {{view.fills.length}} 笔{{view.day?'当日':''}}成交</span></p>
    <template v-if="view.bars.length">
      <TerminalChart class="candles" :plot="plot" label="任务行情与开多、开空、平多、平空成交"/>
      <p>▲ 开多（买开）　▼ 开空（卖开）　● 平多（卖平）　● 平空（买平）。悬停查看时间、成交价和数量。</p>
      <p v-if="view.fills.length>marks.length">有 {{view.fills.length-marks.length}} 笔无法与该合约的 K 线对齐，仍保留在成交明细。</p>
    </template>
    <p v-else-if="view.day">该自然日无可绘制的 K 线。可切换其他日期。</p>
  </template>
</section></template>
<script setup>
import {computed,ref,watch} from 'vue'
import {tradeBarIndex,tradeTime} from '@/js/market-trades.js'
import TerminalChart from '../terminal/TerminalChart.vue'
const props=defineProps({asset:String,mode:String,task:String,strategy:String})
const busy=ref(false),error=ref(''),view=ref(null),code=ref(''),day=ref(''),codes=ref([])
let sequence=0
async function load(symbol='',date=''){
  const ticket=++sequence;busy.value=true;error.value=''
  try{
    const r=await fetch('/mgr/assets/'+props.asset+'/report-chart',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode:props.mode,task:props.task,strategy:props.strategy||'',code:symbol,day:date})})
    const d=await r.json()
    if(d.result!==0)throw Error(d.message||'无法读取成交图')
    if(ticket===sequence){view.value=d;code.value=d.code;day.value=d.day;codes.value=d.codes}
  }catch(e){if(ticket===sequence)error.value=e.message}finally{if(ticket===sequence)busy.value=false}
}
watch(()=>[props.asset,props.mode,props.task,props.strategy],()=>{view.value=null;codes.value=[];load()},{immediate:true})
const periodName=computed(()=>({m1:'1 分钟',m5:'5 分钟',d:'日线'})[view.value?.period])
const colors={开多:'#d94b4b',平多:'#b54444',开空:'#168367',平空:'#12765d'}
const marks=computed(()=>{
  const {bars,fills,period,bar_clock,tick_clock}=view.value
  const index=new Map(bars.map((b,i)=>[String(b.time),i]))
  return fills.flatMap(f=>{
    if(!colors[f.action] || !Number.isFinite(f.price))return []
    const t=String(f.time)
    let n=-1
    if(period==='d'){if(t.length===8 || bar_clock)n=index.get(t.slice(0,8))??-1}
    else if(tick_clock)n=index.get(t.slice(0,12))??-1
    else if(bar_clock && t.length===12 && index.has(t))n=index.get(t)
    else n=tradeBarIndex(f,bars,period)
    return n<0?[]:[{...f,n}]
  })
})
function label(t){return t.length===8?t:t.slice(0,8)+' '+t.slice(8,10)+':'+t.slice(10,12)}
const plot=computed(()=>{
  const {bars}=view.value,stamp=bars.map(b=>String(b.time)),items=marks.value,labels=stamp.map(label)
  const byBar=new Map()
  for(const m of items){if(!byBar.has(m.n))byBar.set(m.n,[]);byBar.get(m.n).push(m)}
  const series=[{name:'K线',type:'candlestick',data:bars.map(b=>[b.open,b.close,b.low,b.high]),itemStyle:{color:'#d94b4b',color0:'#168367',borderColor:'#d94b4b',borderColor0:'#168367'}}]
  for(const [name,color] of Object.entries(colors)){
    const isLong=name.endsWith('多'),open=name.startsWith('开')
    series.push({name,type:'scatter',symbol:open?'triangle':'circle',symbolRotate:isLong?0:180,symbolSize:open?13:9,z:5,
      itemStyle:{color},label:{show:true,formatter:name,position:isLong?'bottom':'top',fontSize:10,color},data:items.filter(m=>m.action===name).map(m=>[labels[m.n],m.price])})
  }
  return {labels,series,zoom:[{type:'inside'},{type:'slider',height:16,bottom:3}],tooltip:{trigger:'axis',renderMode:'richText',formatter(params){
    const x=params.find(p=>p.seriesName==='K线');if(!x)return ''
    const b=bars[x.dataIndex],rows=byBar.get(x.dataIndex)||[]
    return [labels[x.dataIndex],`开 ${b.open}　高 ${b.high}　低 ${b.low}　收 ${b.close}`,...rows.map(r=>
      `${r.action}　${String(r.time).length===8?r.time:tradeTime(r.time)}　价 ${r.price}　数量 ${r.qty}`)].join('\n')
  }}}
})
</script>
<style scoped>.candles{height:350px}.controls{display:flex;gap:16px;flex-wrap:wrap}select{color:inherit;background:var(--wt-surface);padding:5px}p{color:var(--wt-muted)}</style>
