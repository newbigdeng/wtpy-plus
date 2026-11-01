<template><section class="history">
  <header><div><h3>{{symbol}} <small>{{data.meta?.side}} · {{data.meta?.underlying || '商品期权'}}</small></h3><p>{{(data.sources || []).join(' / ')}} <template v-if="latest"> · 最后一根 {{latest.day}}</template></p></div>
    <div class="actions"><div class="periods"><button v-for="p in periods" :key="p[0]" :aria-pressed="period===p[0]" @click="period=p[0]">{{p[1]}}</button></div>
    <input aria-label="历史截止日" type="date" v-model="end"><select v-model.number="count" aria-label="显示根数"><option v-for="n in [20,60,120]" :key="n" :value="n">近 {{n}} 根</option></select>
    <el-button size="small" :loading="busy" @click="load">刷新</el-button></div></header>
  <p role="status" v-if="busy || error || data.note">{{busy?'正在读取所选合约…':error || data.note}}</p>
  <template v-if="!busy && !error">
    <template v-if="latest">
      <TerminalChart class="plot" :plot="plot" label="商品期权 K 线与成交量" @select="i=>selected=bars.slice(-count)[i]?.day"/>
      <div class="barpick"><strong>历史观察</strong><select v-model="selected" aria-label="查看哪根 K 线"><option v-for="b in [...bars].reverse()" :value="b.day" :key="b.day">{{b.day}}</option></select><small>{{bar.source}} · 点击蜡烛可切换</small></div>
      <dl><div v-for="[name,val] in stats" :key="name"><dt>{{name}}</dt><dd>{{val}}</dd></div></dl>
      <p v-if="bar.depth">这根 K 线内的盘口 · {{bar.depth.time}} · 买一 {{fmt(bar.depth.bid)}} / 卖一 {{fmt(bar.depth.ask)}} · 持仓 {{fmt(bar.depth.oi)}} 手</p>
      <p v-else>这根 K 线没有同期盘口快照。</p>
      <h4>波动率与希腊字母</h4>
      <p>{{valuation ? '期权与标的期货按同一根 K 线收盘价估值。' : '缺少同时刻的标的或估值输入。'}} 美式 BAW 模型，年利率 2%，期限按自然日。Theta 按日，Vega / Rho 按一个百分点。</p>
      <dl><div v-for="[name,val] in greeks" :key="name"><dt>{{name}}</dt><dd>{{val}}</dd></div></dl><p v-if="valuation?.reason">{{valuation.reason}}</p>
    </template>
    <p v-else>这个窗口没有可用的 K 线。</p>
    <template v-if="data.meta">
      <h4>盘口与流动性</h4><p>{{snap ? snap.source+' · '+(snap.time || ('交易日 '+(snap.trading_day || '—')+' '+(snap.source_clock || '')+'；读取于 '+snap.observed)) : '没有已记录的盘口快照'}}。此栏独立于上方选中的 K 线。</p>
      <dl><div v-for="[name,val] in depth" :key="name"><dt>{{name}}</dt><dd>{{val}}</dd></div></dl>
      <h4>合约资料</h4><dl><div v-for="[name,val] in terms" :key="name"><dt>{{name}}</dt><dd>{{val ?? '—'}}</dd></div></dl>
    </template>
  </template>
</section></template>
<script setup>
import {computed,ref,watch,onMounted,onBeforeUnmount} from 'vue'
import TerminalChart from '../terminal/TerminalChart.vue'
const props=defineProps({symbol:String})
const data=ref({}),bars=computed(()=>data.value.bars || []),count=ref(60),busy=ref(false),error=ref('')
const periods=[['d','日 K'],['m1','1 分钟'],['m5','5 分钟']],period=ref('d'),end=ref(''),selected=ref('')
const bar=computed(()=>bars.value.find(b=>b.day===selected.value) || latest.value)
const valuation=computed(()=>data.value.valuations?.find(v=>v.day===bar.value?.day))
const snap=computed(()=>data.value.quote)
const latest=computed(()=>bars.value.at(-1))
const fmt=n=>Number.isFinite(n)?n.toLocaleString('zh-CN',{maximumFractionDigits:3}):'—'
const pct=n=>Number.isFinite(n)?fmt(n*100)+'%':'—'
const stats=computed(()=>Object.entries({'开盘':bar.value.open,'最高':bar.value.high,'最低':bar.value.low,'收盘':bar.value.close,'成交量（手）':bar.value.volume,'成交额（元）':bar.value.amount,'持仓量（手）':bar.value.oi}).map(([k,v])=>[k,fmt(v)]))
const greeks=computed(()=>{const v=valuation.value || {};return [['标的收盘',fmt(v.forward)],['内在价值',fmt(v.intrinsic)],['时间价值',fmt(v.time_value)],['距到期（日）',fmt(v.days)],['隐含波动率',pct(v.iv)],...(period.value==='d'?[['标的 20 日历史波动率',pct(v.hv20)]]:[]),...['delta','gamma','theta','vega','rho'].map(k=>[k[0].toUpperCase()+k.slice(1),Number.isFinite(v[k])?Number(v[k].toPrecision(5)).toString():'—'])]})
const depth=computed(()=>{const q=snap.value||{},spread=q.ask>=q.bid && q.bid>0 ? q.ask-q.bid:null;return [['最新价',fmt(q.last)],['昨收 / 昨结',fmt(q.prev_close)+' / '+fmt(q.prev_settle)],['买一 / 手数',fmt(q.bid)+' / '+fmt(q.bid_size)],['卖一 / 手数',fmt(q.ask)+' / '+fmt(q.ask_size)],['买卖价差',fmt(spread)],['相对价差',pct(spread!=null?spread/((q.ask+q.bid)/2):null)],['累计成交量（手）',fmt(q.volume)],['累计成交额（元）',fmt(q.amount)],['持仓量（手）',fmt(q.oi)]]})
const terms=computed(()=>{const m=data.value.meta||{};return Object.entries({'交易所':m.exchange,'标的期货':m.underlying,'方向':m.side,'行权价':m.strike,'到期日':String(m.expiry||'—').replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3'),'合约乘数':m.multiplier,'最小变动价位':m.tick,'资料观察日':m.observed})})
const plot=computed(()=>{
  const all=bars.value,rows=all.slice(-count.value),start=all.length-rows.length
  const series=[{name:periods.find(p=>p[0]===period.value)[1],type:'candlestick',data:rows.map(b=>[b.open,b.close,b.low,b.high]),itemStyle:{color:'#c44545',borderColor:'#c44545',color0:'#208268',borderColor0:'#208268'}}]
  for(const n of [5,20])series.push({name:'MA'+n,data:rows.map((_,i)=>i+start+1<n?null:all.slice(i+start+1-n,i+start+1).reduce((sum,b)=>sum+b.close,0)/n)})
  return {labels:rows.map(b=>b.day),series,volumes:rows.map(b=>({value:b.volume,itemStyle:{color:b.close>=b.open?'#c44545':'#208268'}})),
    zoom:[{type:'inside'},{type:'slider',height:16,bottom:2}],
    tooltip:{trigger:'axis',renderMode:'richText',formatter(params){const b=rows[params[0]?.dataIndex];return b?[b.day,'开 '+fmt(b.open)+'   高 '+fmt(b.high),'低 '+fmt(b.low)+'   收 '+fmt(b.close),'成交量 '+fmt(b.volume)+' 手'].join('\n'):''}}}
})
let request,timer
async function load(quiet=false){
  clearTimeout(timer)
  if(document.hidden)return
  request?.abort();const ctrl=new AbortController();request=ctrl
  const pinned=quiet===true && selected.value!==latest.value?.day ? selected.value : ''
  if(quiet!==true){data.value={};selected.value='';busy.value=true}
  error.value=''
  try{
    const r=await fetch('/mgr/assets/option/watch',{method:'POST',signal:ctrl.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:props.symbol,period:period.value,end:end.value||null})})
    const result=await r.json();if(ctrl.signal.aborted)return
    if(result.result!==0)throw Error(result.message)
    data.value=result;selected.value=result.bars.some(b=>b.day===pinned)?pinned:result.bars.at(-1)?.day || ''
  }catch(e){if(!ctrl.signal.aborted)error.value=e.message}finally{if(!ctrl.signal.aborted){busy.value=false;if(!end.value)timer=setTimeout(()=>load(true),60000)}}
}
function visibility(){clearTimeout(timer);request?.abort();if(!document.hidden)load(false)}
watch([()=>props.symbol,period,end],()=>load(false),{immediate:true})
onMounted(()=>document.addEventListener('visibilitychange',visibility))
onBeforeUnmount(()=>{clearTimeout(timer);request?.abort();document.removeEventListener('visibilitychange',visibility)})
</script>
<style scoped>
.history{margin-top:18px;border:1px solid var(--wt-line);border-radius:6px;padding:16px;background:var(--wt-surface)}header,.actions,dl{display:flex;align-items:center;gap:10px;flex-wrap:wrap}header{justify-content:space-between}h3,p,dl,dd{margin:0}small,p,dt{font-size:12px;color:var(--wt-muted)}p{margin-top:5px}dl{gap:24px;margin:15px 0}dd{font-variant-numeric:tabular-nums;font-size:16px;margin-top:4px}.plot{height:365px}select{background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line);padding:5px}
.periods{display:flex}.periods button{background:transparent;border:1px solid var(--wt-line);padding:6px 9px;color:inherit;cursor:pointer}.periods [aria-pressed=true]{background:var(--wt-text);color:var(--wt-surface)}input{color:inherit;background:inherit;border:1px solid var(--wt-line);padding:4px}.barpick{display:flex;align-items:center;gap:12px;flex-wrap:wrap}h4{border-top:1px solid var(--wt-line);padding-top:14px;margin-bottom:6px}
</style>
