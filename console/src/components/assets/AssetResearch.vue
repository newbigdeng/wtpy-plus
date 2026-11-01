<template>
  <section class="asset-research">
    <h2>{{backtest?'原生策略回测':'本地历史数据'}} · {{name}}</h2>
    <button @click="load">刷新</button><p role="status">{{message}}</p>
    <template v-if="backtest">
      <OptionExpiry v-if="asset==='option'" :api="api" :runs="runs"/>
      <form @submit.prevent="run">
        <label>策略版本<select v-model="selected"><option v-for="s in strategies" :key="s.id" :value="s">{{s.name}} · v{{s.version}}</option></select></label>
        <template v-if="asset==='option'">
          <label>数据周期<select v-model="period"><option value="">全部</option><option v-for="p in ['tick','m1','m5','d']" :key="p">{{p}}</option></select></label>
          <label>期权方向<select v-model="side"><option value="">全部</option><option value="C">看涨</option><option value="P">看跌</option></select></label>
          <label>标的筛选<input v-model.trim="filter" placeholder="如 DCE.m2701"></label>
        </template>
        <label>主行情数据<select v-model="dataset"><option v-for="d in mainChoices" :key="d.id" :value="d.id">{{d.label}}</option></select></label>
        <label v-if="asset==='option'">其他期权或标的期货<select v-model="companions" multiple><option v-for="d in otherChoices" :key="d.id" :value="d.id">{{d.label}}</option></select></label>
        <label>开始<input type="date" v-model="start" required></label><label>结束<input type="date" v-model="end" required></label>
        <label>初始资金<input type="number" min="1" step="any" v-model.number="capital" required></label>
        <label v-if="asset==='option'">单边手续费 / 元每手<input type="number" min="0" step="any" v-model.number="fee" placeholder="留空使用合约费表"></label>
        <template v-if="tickCTA">
          <label>盘口撮合<select v-model="fill"><option value="causal_touch">报价触达</option><option value="volume_limited">按盘口数量限制成交</option></select></label>
          <label>延迟事件数<input type="number" min="0" max="1000" v-model.number="delay" required></label>
          <label v-if="fill==='volume_limited'">盘口参与率 %<input type="number" min="0.01" max="100" step="any" v-model.number="participation" required></label>
        </template>
        <button :disabled="busy || !selected || !dataset">运行所选策略</button>
      </form>
      <section v-if="asset==='option'">
        <p>各腿按原生行情回放。要看历史 Greeks，请一起选择同工作区的标的期货。分钟线和日线只用闭合价格，无法还原盘中止损和盘口深度。</p>
        <table v-if="pool.length"><thead><tr><th>选中合约</th><th>标的</th><th>行权价</th><th>到期日</th><th>记录的交易日数</th></tr></thead><tbody>
          <tr v-for="d in pool" :key="d.id"><td>{{d.code}} · {{d.period}}</td><td>{{d.underlying}}</td><td>{{d.strike ?? '—'}}</td><td>{{d.expiry}}</td><td>{{d.dates?.length}}</td></tr></tbody></table>
        <button v-if="pool.length" :disabled="!days.length" @click="useDays">使用共同区间</button> <small v-if="pool.length">到期前共有 {{days.length}} 个有记录的交易日</small>
        <p v-if="!datasets.length">期权工作区尚无已采集或导入的行情。</p>
      </section>
      <h3>我的回测记录</h3><p v-if="!runs.length">此资产下还没有任务。</p>
      <table v-else><thead><tr><th>策略</th><th>合约</th><th>启动时间</th><th>状态</th><th>任务</th></tr></thead>
      <tbody><tr v-for="r in runs" :key="r.id"><td>{{r.strategy}}</td><td>{{r.dataset?.code}}</td><td>{{r.runtime}}</td><td>{{r.status}}</td><td><button @click="readDetail(r)">查看结果</button></td></tr></tbody></table>
      <section v-if="detail"><h3>任务结果 · {{detail.status}}</h3><p v-if="detail.error">{{detail.error}}</p>
        <p v-if="asset==='option' && activeRun">撮合 {{activeRun.fill_config?.fill_model || activeRun.strategy_type}} · 延迟 {{activeRun.fill_config?.event_delay ?? 0}} 个事件 · 手续费 {{activeRun.dataset?.fee_per_lot!=null ? activeRun.dataset.fee_per_lot+' 元/手/边' : '合约费表'}}</p>
        <dl><template v-for="(value,key) in detail.summary" :key="key"><dt>{{key}}</dt><dd>{{value}}</dd></template></dl>
        <section v-if="asset==='option' && activeRun?.dataset?.option_legs?.length && detail.status!=='running'">
          <h4>按交易日复算组合风险</h4><label>交易日<input type="date" v-model="riskDay"></label>
          <label>估值年利率 %<input type="number" min="0" max="50" step="any" v-model.number="rate"></label>
          <button :disabled="!riskDay || riskBusy" @click="readRisk">读取历史风险</button>
          <p>按分钟末成交持仓乘合约乘数，按标的分别汇总。Tick 用双边中价，K 线用收盘价。缺少同步报价的风险值留空。</p>
          <template v-if="risk"><p>有行情的时间点 {{risk.sampled_minutes}} 个，缺少估值的持仓截面 {{risk.missing_rows}} 个。</p>
          <div class="risk-table"><table><thead><tr><th v-for="h in ['时间','标的','Delta','Gamma','Theta','Vega','Rho','缺数原因']" :key="h">{{h}}</th></tr></thead><tbody>
            <tr v-for="g in risk.rows" :key="g.time+g.underlying"><td>{{g.time}}</td><td>{{g.underlying}}</td><td v-for="k in ['delta','gamma','theta','vega','rho']" :key="k">{{g[k]?.toFixed(3) ?? '—'}}</td><td>{{g.missing?.join('，')}}</td></tr></tbody></table></div></template>
        </section>
      </section>
    </template>
    <template v-else>
      <p v-if="!datasets.length">这里仅显示本资产采集器已经写入的行情。</p>
      <p v-if="asset==='crypto' && datasets.length">逐笔按成交编号去重。分钟数含首尾时段，回放只使用完整分钟。</p>
      <table v-if="datasets.length"><thead><tr><th>合约</th><th>周期</th><th>记录数</th><th>首条</th><th>末条</th></tr></thead>
      <tbody><tr v-for="d in datasets" :key="d.id"><td>{{d.code}}</td><td>{{d.period}}</td><td>{{d.rows}}</td><td>{{d.start}}</td><td>{{d.end}}</td></tr></tbody></table>
    </template>
  </section>
</template>
<script setup>
import {computed,ref,onMounted,watch} from 'vue'
import OptionExpiry from './OptionExpiry.vue'
const props=defineProps({asset:String,name:String,backtest:Boolean})
const detail=ref(null)
const datasets=ref([]),strategies=ref([]),runs=ref([]),selected=ref(null),dataset=ref('')
const start=ref(''),end=ref(''),capital=ref(100000),message=ref(''),busy=ref(false)
const companions=ref([])
const fee=ref('')
const period=ref(''),side=ref(''),filter=ref(''),fill=ref('causal_touch'),delay=ref(0),participation=ref(10)
const activeRun=ref(null),risk=ref(null),riskDay=ref(''),rate=ref(2),riskBusy=ref(false)
const mainChoices=computed(()=>props.asset!=='option'?datasets.value:datasets.value.filter(d=>d.leg_type==='option' &&
  (!period.value||d.period===period.value) && (!side.value||d.side===side.value) && (d.underlying||'').toLowerCase().includes(filter.value.toLowerCase())))
const primary=computed(()=>datasets.value.find(d=>d.id===dataset.value))
const tickCTA=computed(()=>props.asset==='option' && primary.value?.period==='tick' && selected.value?.engine_type==='CTA')
const otherChoices=computed(()=>datasets.value.filter(d=>primary.value && d.code!==primary.value.code && ((d.period==='tick')===(primary.value.period==='tick'))))
const pool=computed(()=>datasets.value.filter(d=>d.id===dataset.value || companions.value.includes(d.id)))
const days=computed(()=>!primary.value?[]:(primary.value.dates||[]).filter(day=>pool.value.every(d=>d.dates?.includes(day) && day<d.expiry)))
function iso(day){return String(day).replace(/^(\d{4})(\d\d)(\d\d)$/,'$1-$2-$3')}
function useDays(){start.value=iso(days.value[0]);end.value=iso(days.value.at(-1))}
watch([period,side,filter],()=>{if(!mainChoices.value.some(d=>d.id===dataset.value))dataset.value=''})
watch(dataset,()=>{companions.value=[]})
async function api(action,body={}){
  const r=await fetch('/mgr/assets/'+props.asset+'/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  const data=await r.json()
  if(data.result!==0)throw new Error(data.message||'请求失败')
  return data
}
async function load(){
  try{
    datasets.value=(await api('history')).datasets
    if(props.backtest){
      const [library,tasks]=await Promise.all([api('strategy_library'),api('runs')])
      strategies.value=library.strategies;runs.value=tasks.runs
    }
    message.value=''
  }catch(error){message.value=error.message}
}
async function readDetail(row){
  risk.value=null;activeRun.value=row;detail.value=null
  try{const data=await api('detail',{strategy:row.straid,task:row.id});if(activeRun.value?.id===row.id)detail.value=data.detail}catch(error){message.value=error.message}
}
async function readRisk(){
  const row=activeRun.value;riskBusy.value=true;risk.value=null
  try{const report=await api('replay-risk',{strategy:row.straid,task:row.id,day:riskDay.value,rate:rate.value/100})
    if(activeRun.value?.id===row.id)risk.value=report
  }catch(e){message.value=e.message}finally{riskBusy.value=false}
}
async function run(){
  if(busy.value)return
  busy.value=true
  try{
    const execution=tickCTA.value?{fill_model:fill.value,event_delay:delay.value,participation_rate:fill.value==='volume_limited'?participation.value/100:0}:{}
    await api('run',{library_id:selected.value.id,version:selected.value.version,dataset:dataset.value,companions:companions.value,start:start.value,end:end.value,capital:capital.value,...execution,...(props.asset==='option' && fee.value!==''?{fee_per_lot:fee.value}:{})})
    await load()
  }catch(error){message.value=error.message}finally{busy.value=false}
}
onMounted(load)
</script>
<style scoped>.risk-table{max-height:420px;overflow:auto}</style>
<style scoped>.asset-research{padding:22px;overflow:auto;color:var(--wt-text)}form{display:flex;gap:12px;flex-wrap:wrap;align-items:end}label{display:grid;gap:5px}button,input,select{padding:6px;background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line)}button{cursor:pointer}th,td{text-align:left;padding:7px}p{color:var(--wt-muted)}</style>
