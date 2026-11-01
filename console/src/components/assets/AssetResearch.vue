<template>
  <section class="asset-research" :class="{'asset-lab':backtest}">
    <aside v-if="backtest" class="library wt-research-sidebar" aria-label="回测策略列表">
      <div class="wt-research-heading">策略研究 <el-button link @click="load">刷新</el-button></div>
      <el-input v-model="search" clearable placeholder="搜索策略" aria-label="搜索回测策略"/>
      <p>策略库 · {{filtered.length}} 个版本</p>
      <button v-for="s in filtered" :key="s.id" class="strategy-card" :aria-pressed="selected?.id===s.id" @click="choose(s)">
        <strong>{{s.name}}</strong><small>{{s.engine_type}} · v{{s.version}}</small></button>
      <p v-if="!filtered.length">{{search?'没有匹配的策略。':'该资产还没有库策略。'}}</p>
      <router-link :to="{path:'/strategies',query:{asset}}">打开{{name}}策略管理 →</router-link>
    </aside>
    <div class="content">
    <header v-if="backtest"><div><small>RESEARCH / BACKTEST</small><h1>{{selected?.name || name+'策略回测'}}</h1></div>
      <router-link :to="{path:'/api-manual',query:{type:selected?.engine_type?.toLowerCase() || 'cta'}}" target="_blank">策略 API 手册 ↗</router-link></header>
    <template v-else><h2>本地历史数据 · {{name}}</h2><button @click="load">刷新</button></template>
    <p v-if="message" role="status">{{message}}</p>
    <template v-if="backtest">
      <p>{{tips[asset]}}</p>
      <MainReplayPanel v-if="asset==='option'&&selected?.engine_type==='CTA'" asset="option" :strategy="selected.id" :version="selected.version"  :symbol="optionSymbol" :dataset="primary?.period==='tick'?primary.id:''"/>
      <div class="modes" role="group" aria-label="回测工作区视图">
        <button v-for="[id,label] in panels" :key="id" :aria-pressed="panel===id" @click="panel=id">{{label}}</button>
      </div>
      <OptionExpiry v-if="panel==='expiry'" :api="api" :runs="runs"/>
      <FactoryResearch v-else-if="panel==='factory'" :api="api" @launched="load"/>
      <section v-else-if="panel==='code'">
        <h3>{{selected?selected.name+' · v'+selected.version:'尚未选择策略'}}</h3><p>{{selected?.description}}</p>
        <router-link v-if="selected && manage" :to="{path:'/strategies',query:{asset,edit:selected.id}}">在策略管理中编辑新版本 →</router-link>
        <p v-if="selected && !manage">当前角色不能读取策略源码。</p>
        <pre v-else-if="source">{{source}}</pre><p v-else-if="selected">{{sourceError || '正在读取此版本…'}}</p>
      </section>
      <section v-else-if="panel==='config'" class="config"><h3>原生回测配置</h3>
      <p v-if="selected">{{selected.engine_type}} · 固定版本 v{{selected.version}}，启动时复制到本账户的回测目录。</p>
      <form @submit.prevent="run">
        <template v-if="asset==='option'">
          <label>数据周期<select v-model="period"><option value="">全部</option><option v-for="p in ['tick','m1','m5','d']" :key="p">{{p}}</option></select></label>
          <label>期权方向<select v-model="side"><option value="">全部</option><option value="C">看涨</option><option value="P">看跌</option></select></label>
          <label>标的筛选<input v-model.trim="filter" placeholder="如 DCE.m2701"></label>
        </template>
        <label>主行情数据<select v-model="dataset"><option value="">请选择数据集</option><option v-for="d in mainChoices" :key="d.id" :value="d.id">{{d.label}}</option></select></label>
        <label v-if="asset==='option'">其他期权或标的期货<select v-model="companions" multiple><option v-for="d in otherChoices" :key="d.id" :value="d.id">{{d.label}}</option></select></label>
        <label>开始<input type="date" v-model="start" required></label><label>结束<input type="date" v-model="end" required></label>
        <label>初始资金<input type="number" min="1" step="any" v-model.number="capital" required></label>
        <label v-if="asset==='option'">单边手续费 / 元每手<input type="number" min="0" step="any" v-model.number="fee" placeholder="留空使用合约费表"></label>
        <template v-if="tickCTA">
          <label>盘口撮合<select v-model="fill"><option value="causal_touch">报价触达</option><option value="volume_limited">按盘口数量限制成交</option></select></label>
          <label>延迟事件数<input type="number" min="0" max="1000" v-model.number="delay" required></label>
          <label v-if="fill==='volume_limited'">盘口参与率 %<input type="number" min="0.01" max="100" step="any" v-model.number="participation" required></label>
        </template>
        <button :disabled="busy || !selected || !validDates">{{busy?'正在提交…':'启动原生回测'}}</button>
      </form>
      <p v-if="primary">数据覆盖 {{iso(primary.start)}} 至 {{iso(primary.end)}} · {{primary.rows}} 条</p>
      <p v-if="!mainChoices.length">当前筛选没有可回放的数据。<router-link :to="{path:'/history',query:{asset}}">查看{{name}}历史数据</router-link></p>
      <section v-if="asset==='option'">
        <p>各腿按原生行情回放。要看历史 Greeks，请一起选择同工作区的标的期货。分钟线和日线只用闭合价格，无法还原盘中止损和盘口深度。</p>
        <table v-if="pool.length"><thead><tr><th>选中合约</th><th>标的</th><th>行权价</th><th>到期日</th><th>记录的交易日数</th></tr></thead><tbody>
          <tr v-for="d in pool" :key="d.id"><td>{{d.code}} · {{d.period}}</td><td>{{d.underlying}}</td><td>{{d.strike ?? '—'}}</td><td>{{d.expiry}}</td><td>{{d.dates?.length}}</td></tr></tbody></table>
        <button v-if="pool.length" :disabled="!days.length" @click="useDays">使用共同区间</button> <small v-if="pool.length">到期前共有 {{days.length}} 个有记录的交易日</small>
        <p v-if="!datasets.length">期权工作区尚无已采集或导入的行情。</p>
      </section>
      </section>
      <section v-else><h3>我的回测记录 <el-button link @click="load">刷新任务</el-button></h3><p v-if="!runs.length">此资产下还没有任务。</p>
      <table v-else><thead><tr><th>策略</th><th>合约</th><th>启动时间</th><th>状态</th><th>任务</th></tr></thead>
      <tbody><tr v-for="r in runs" :key="r.id"><td>{{r.strategy}}</td><td>{{r.dataset?.code}}</td><td>{{r.runtime}}</td><td>{{statusText(r.status)}}</td><td><router-link :to="{name:'run-report',query:{asset,strategy:r.straid,task:r.id}}">统计报告</router-link> · <button @click="readDetail(r)">任务详情</button> <DeleteRun :asset="asset" :task="r.id" :title="r.strategy" :status="r.status" @deleted="deleted"/></td></tr></tbody></table>
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
      </section></section>
    </template>
    <template v-else>
      <p v-if="!datasets.length">这里仅显示本资产采集器已经写入的行情。</p>
      <p v-if="asset==='crypto' && datasets.length">逐笔按成交编号去重。分钟数含首尾时段，回放只使用完整分钟。</p>
      <table v-if="datasets.length"><thead><tr><th>合约</th><th>周期</th><th>记录数</th><th>首条</th><th>末条</th></tr></thead>
      <tbody><tr v-for="d in datasets" :key="d.id"><td>{{d.code}}</td><td>{{d.period}}</td><td>{{d.rows}}</td><td>{{d.start}}</td><td>{{d.end}}</td></tr></tbody></table>
    </template>
    </div>
  </section>
</template>
<script setup>
import MainReplayPanel from '../backtest/MainReplayPanel.vue'
import {computed,ref,onMounted,onUnmounted,watch} from 'vue'
import OptionExpiry from './OptionExpiry.vue'
import DeleteRun from '../backtest/DeleteRun.vue'
import FactoryResearch from './FactoryResearch.vue'
import {useRoute} from 'vue-router'
const props=defineProps({asset:String,name:String,backtest:Boolean})
const route=useRoute()
const search=ref(''),panel=ref(props.asset==='option' && route.query.panel==='factory'?'factory':'config'),source=ref(''),sourceError=ref(''),manage=ref(false)
const panels=computed(()=>[['config','原生回测配置'],['code','策略代码'],['tasks','任务记录'],...(props.asset==='option'?[['factory','C++ 期权策略'],['expiry','跨期清算']]:[])])
const tips={stock:'使用股票工作区的合约、交易时段和历史数据。',option:'多腿行情在同一任务回放。跨期清算用于检查移仓、行权和指派。',crypto:'逐笔成交没有买卖盘口。选择完整分钟线回放，不能据此模拟排队成交。'}
const statusText=s=>({running:'运行中',completed:'已完成',failed:'失败',interrupted:'已中断',stopped:'已停止'})[s] || s
const detail=ref(null)
const datasets=ref([]),strategies=ref([]),runs=ref([]),selected=ref(null),dataset=ref('')
const start=ref(''),end=ref(''),capital=ref(100000),message=ref(''),busy=ref(false)
const companions=ref([])
const fee=ref('')
const period=ref(''),side=ref(''),filter=ref(''),fill=ref('causal_touch'),delay=ref(0),participation=ref(10)
const activeRun=ref(null),risk=ref(null),riskDay=ref(''),rate=ref(2),riskBusy=ref(false)
function deleted(id){
  if(activeRun.value?.id===id){activeRun.value=null;detail.value=null;risk.value=null}
  load()
}
const filtered=computed(()=>strategies.value.filter(s=>[s.name,s.id,s.engine_type].join(' ').toLowerCase().includes(search.value.toLowerCase())))
const mainChoices=computed(()=>datasets.value.filter(d=>d.rows>0 && d.period!=='trades' && (selected.value?.engine_type!=='HFT' || d.period==='tick') &&
  (props.asset!=='option' || (d.leg_type==='option' && (!period.value||d.period===period.value) && (!side.value||d.side===side.value) && (d.underlying||'').toLowerCase().includes(filter.value.toLowerCase())))))
const primary=computed(()=>datasets.value.find(d=>d.id===dataset.value))
const optionSymbol=computed(()=>{const m=primary.value?.code?.match(/^([A-Z]+)\.([A-Za-z]+)(\d{4})\.([CP])\.(\d+)$/);if(!m)return '';const sep=m[1]==='DCE'||m[1]==='GFEX'?'-':'';return m[1]+'.'+m[2]+(m[1]==='CZCE'?m[3].slice(1):m[3])+sep+m[4]+sep+m[5]})
const tickCTA=computed(()=>props.asset==='option' && primary.value?.period==='tick' && selected.value?.engine_type==='CTA')
const otherChoices=computed(()=>datasets.value.filter(d=>primary.value && d.code!==primary.value.code && ((d.period==='tick')===(primary.value.period==='tick'))))
const pool=computed(()=>datasets.value.filter(d=>d.id===dataset.value || companions.value.includes(d.id)))
const days=computed(()=>!primary.value?[]:(primary.value.dates||[]).filter(day=>pool.value.every(d=>d.dates?.includes(day) && day<d.expiry)))
const validDates=computed(()=>primary.value && start.value && end.value && start.value<=end.value &&
  (props.asset==='option'?days.value.some(d=>iso(d)>=start.value && iso(d)<=end.value) && pool.value.every(d=>end.value<iso(d.expiry)):
  start.value<=iso(primary.value.end) && end.value>=iso(primary.value.start)))
function iso(day){return String(day).slice(0,8).replace(/^(\d{4})(\d\d)(\d\d)$/,'$1-$2-$3')}
function useDays(){start.value=iso(days.value[0]);end.value=iso(days.value.at(-1))}
watch(mainChoices,choices=>{if(!choices.some(d=>d.id===dataset.value))dataset.value=choices[0]?.id||''})
watch(dataset,()=>{companions.value=[]
  if(props.asset==='option' && days.value.length)useDays()
  else {start.value=primary.value?iso(primary.value.start):'';end.value=primary.value?iso(primary.value.end):''}
})
function choose(s){selected.value=s}
watch([panel,selected,manage],async ([view,s,can],_,onCleanup)=>{
  let current=true;onCleanup(()=>current=false);source.value='';sourceError.value=''
  if(view!=='code'||!can||!s)return
  try{const result=await api('strategy_library_get',{library_id:s.id,version:s.version})
    if(current)source.value=result.strategy.source
  }catch(e){if(current)sourceError.value=e.message}
})
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
      strategies.value=library.strategies;manage.value=library.can_manage
      selected.value=strategies.value.find(s=>s.id===selected.value?.id) || strategies.value[0] || null
      runs.value=tasks.runs
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
    await load();panel.value='tasks'
  }catch(error){message.value=error.message}finally{busy.value=false}
}
onMounted(load)
let timer
watch(()=>runs.value.some(r=>r.status==='running'),active=>{
  clearInterval(timer)
  if(active)timer=setInterval(async()=>{try{runs.value=(await api('runs')).runs}catch(e){message.value=e.message}},5000)
})
onUnmounted(()=>clearInterval(timer))
</script>
<style scoped>.risk-table{max-height:420px;overflow:auto}</style>
<style scoped>
.asset-research.asset-lab{display:grid;grid-template-columns:clamp(210px,21vw,320px) minmax(0,1fr);height:100%;padding:0;overflow:hidden}
.asset-lab .content{padding:18px 22px;min-width:0;overflow:auto}
.library{box-sizing:border-box;border-right:1px solid var(--wt-line)}
.strategy-card{display:grid;gap:4px;width:100%;margin:5px 0;padding:10px;text-align:left;border-radius:6px}
button[aria-pressed="true"]{outline:1px solid var(--wt-text);background:var(--wt-hover)}
header,.modes{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.modes{justify-content:flex-start;padding:10px 0;border-bottom:1px solid var(--wt-line)}
header h1{margin:4px 0;font-size:20px}small{color:var(--wt-muted)}a{color:var(--wt-text)}
.config form{padding:12px 0}pre{padding:12px;overflow:auto;background:var(--wt-sidebar);font:13px/1.6 var(--wt-mono)}
@media(max-width:760px){.asset-research.asset-lab{display:block;height:auto;overflow:visible}.library{height:auto;max-height:220px;border-right:0;border-bottom:1px solid var(--wt-line)}}
</style>
<style scoped>.asset-research{padding:22px;overflow:auto;color:var(--wt-text)}form{display:flex;gap:12px;flex-wrap:wrap;align-items:end}label{display:grid;gap:5px}button,input,select{padding:6px;background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line)}button{cursor:pointer}th,td{text-align:left;padding:7px}p{color:var(--wt-muted)}</style>
