<template><article class="run-report">
  <router-link :to="{name:'backtest-reports',query:{asset}}">← 回测报告列表</router-link>
  <header><div><small>{{assetName}} / {{modeName}}</small><h1>回测统计报告</h1><p>{{strategyName}} · {{task}}</p></div><button @click="load" :disabled="busy">刷新报告</button></header>
  <p v-if="busy || error" role="status">{{error || '正在读取任务流水…'}}</p>
  <ReportCandles v-if="task && assetName && modeName" :asset="asset" :mode="mode" :task="task" :strategy="route.query.strategy"/>
  <template v-if="report"><p>任务状态 {{statusLabel}} <span v-if="report.error"> · {{report.error}}</span></p>
    <section class="cards"><div v-for="[label,value] in metrics" :key="label"><small>{{label}}</small><strong>{{value}}</strong></div></section>
    <h2>{{hasEquity?'资金曲线':'累计净盈亏曲线'}}</h2>
    <TerminalChart v-if="points.length" class="chart" :plot="equityPlot" label="回测资金曲线"/><p v-else>该任务没有可用的资金记录。</p>
    <p>{{note}}</p>
    <template v-if="hasEquity && points.length && capital>0"><h2>回撤（%）</h2><TerminalChart class="chart drawdown" :plot="ddPlot" label="回测回撤曲线"/></template>
    <h2>{{mode==='expiry'?'清算与成交流水':'成交明细'}}</h2>
    <p v-if="total==null">任务未记录成交流水。</p><p v-else-if="!total">账簿中有 0 笔记录。</p>
    <template v-else><p>共 {{total}} 笔 · 第 {{page}} / {{pages}} 页 <small v-if="mode==='research' && total>report.trades.length">研究快照保存前 {{report.trades.length}} 笔</small></p>
      <div class="scroll"><table><thead><tr><th v-for="[key,title] in columns" :key="key">{{title}}</th></tr></thead><tbody>
        <tr v-for="(row,i) in shown" :key="i"><td v-for="[key] in columns" :key="key">{{row[key] ?? '—'}}</td></tr>
      </tbody></table></div><button :disabled="page<=1 || busy" @click="turn(-1)">上一页</button> <button :disabled="page>=pages || busy" @click="turn(1)">下一页</button>
    </template>
    <template v-if="mode==='expiry'"><h2>期末持仓</h2><p v-for="[code,qty] in positions" :key="code">{{code}}　{{qty}} 手</p></template>
  </template>
</article></template>
<script setup>
import {ref,computed,watch} from 'vue'
import {useRoute} from 'vue-router'
import TerminalChart from '../terminal/TerminalChart.vue'
import ReportCandles from './ReportCandles.vue'
const route=useRoute(),report=ref(null),page=ref(1),busy=ref(false),error=ref('')
const asset=computed(()=>route.query.asset||'futures'),mode=computed(()=>route.query.mode||'native'),task=computed(()=>route.query.task)
const assetName=computed(()=>({stock:'股票',option:'商品期权',crypto:'加密货币',futures:'期货'})[asset.value])
const modeName=computed(()=>({native:'原生策略',plus:'Plus 仿真',research:'研究实验',expiry:'跨期清算'})[mode.value])
const strategyName=computed(()=>report.value?.name||route.query.strategy||modeName.value)
const statusLabel=computed(()=>({completed:'已完成',running:'运行中',failed:'失败',interrupted:'已中断',stopped:'已停止'})[report.value?.status]||report.value?.status)
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  if(!r.ok)throw Error('报告接口返回 '+r.status)
  const d=await r.json();if(d.result!==0)throw Error(d.message||'读取任务失败');return d
}
let sequence=0
async function load(){
  const id=++sequence;busy.value=true;error.value='';report.value=null
  const q=route.query,offset=(page.value-1)*100
  try{
    let value
    if(!assetName.value||!modeName.value||!task.value)throw Error('报告地址缺少有效任务')
    if(mode.value==='expiry' && asset.value==='option'){
      const {report:r}=await post('/mgr/assets/option/expiry-detail',{run:q.task})
      value={name:r.old+' → '+r.new,status:'completed',capital:r.initial,points:r.daily.map(d=>[d.day,d.equity]),summary:{},trades:r.events,total:r.events.length,fees:r.events.reduce((s,e)=>s+e.fee,0),final:r.final}
    }else if(mode.value==='plus' && asset.value==='futures'){
      const d=await post('/bt/plus/detail',{taskid:q.task,ledger:'trades',offset,limit:100})
      value={name:d.task.label||d.task.straid,status:d.task.status,error:d.task.error,capital:d.summary?.capital??d.task.capital,points:d.equity_curve||[],summary:d.summary,trades:d.ledger_page?.rows||[],total:d.ledger_totals?.trades,fees:d.summary?.commission}
    }else if(mode.value==='research' && asset.value==='futures'){
      const d=await post('/mgr/research/experiment_detail',{experiment_id:q.task})
      const cap=d.experiment.capital
      value={name:d.project?.title,status:d.experiment.status,error:d.experiment.error,capital:cap,summary:d.summary||{},
        points:(d.funds||[]).map(f=>[f.date,(cap??0)+f.dynbalance]),trades:d.trades||[],total:d.metrics?.trade_count,fees:d.metrics?.fee}
    }else if(mode.value==='native' && q.strategy){
      let d
      if(asset.value==='futures'){
        const body={straid:q.strategy,btid:q.task}
        d=await post('/bt/qrybtdetail',body)
        const [funds,trades]=await Promise.all(['/bt/qrybtfunds','/bt/qrybttrds'].map(p=>post(p,body)))
        d.funds=funds.funds;d.trades=trades.trades;d.trade_count=d.trades?.length
        if(d.summary?.capital==null){
          const list=await post('/bt/qrystrabts',{straid:q.strategy})
          d.capital=list.backtests?.find(r=>r.id===q.task)?.capital
        }
      }else{
        d=(await post('/mgr/assets/'+asset.value+'/detail',{strategy:q.strategy,task:q.task,report:true,offset})).detail
      }
      const cap=d.capital??d.summary?.capital
      value={status:d.status,error:d.error,capital:cap,points:(d.funds||[]).map(f=>[f.date,(cap??0)+f.dynbalance]),summary:d.summary||{},trades:d.trades||[],total:d.trade_count,fees:d.funds?.at(-1)?.fee}
    }else throw Error('报告类型与资产不匹配')
    if(id===sequence)report.value=value
  }catch(e){if(id===sequence)error.value=e.message}finally{if(id===sequence)busy.value=false}
}
watch(()=>route.fullPath,()=>{page.value=1;load()},{immediate:true})
function turn(delta){page.value+=delta;if(mode.value==='plus'||(mode.value==='native' && asset.value!=='futures'))load()}
const capital=computed(()=>report.value?.capital),total=computed(()=>report.value?.total),pages=computed(()=>Math.ceil((mode.value==='research'?report.value?.trades?.length:total.value)/100))
const points=computed(()=>(report.value?.points||[]).filter(p=>Number.isFinite(p[1])))
const hasEquity=computed(()=>mode.value!=='native'||capital.value!=null)
const shown=computed(()=>['research','expiry'].includes(mode.value)||(mode.value==='native' && asset.value==='futures')?report.value.trades.slice((page.value-1)*100,page.value*100):report.value.trades)
const equityPlot=computed(()=>({labels:points.value.map(p=>String(p[0])),series:[{name:hasEquity.value?'账户权益':'累计净盈亏',data:points.value.map(p=>p[1])}]}))
const drawdowns=computed(()=>{let peak=capital.value;return points.value.map(p=>{peak=Math.max(peak,p[1]);return peak>0?(p[1]/peak-1)*100:null})})
const ddPlot=computed(()=>({labels:equityPlot.value.labels,series:[{name:'回撤 %',data:drawdowns.value}]}))
const note=computed(()=>({research:'研究任务的资金曲线使用原生日结资金流水。',native:'日结权益取初始资金加累计净盈亏。手续费已计入日结净盈亏。',plus:'资金点来自模拟柜台。长序列保留分段高低点，回撤图按展示点计算。',expiry:'日终权益等于现金余额加期权市值，期货按当日结算价盯市。'})[mode.value])
const positions=computed(()=>Object.entries({...report.value.final.options,...report.value.final.futures}))
function fmt(v,suffix=''){return Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2})+suffix:'—'}
const metrics=computed(()=>{
  const r=report.value,last=points.value.at(-1)?.[1],s=r.summary||{},cap=capital.value
  const ret=s.total_return??(cap>0 && last!=null?(last/cap-1)*100:null)
  const dd=s.max_falldown??(cap>0 && points.value.length?drawdowns.value.reduce((peak,v)=>Math.max(peak,-v),0):null)
  const m=[['初始资金',fmt(cap)],['期末权益',fmt(hasEquity.value?last:null)],['总收益率',fmt(ret,'%')],[mode.value==='plus'?'展示点最大回撤':'最大回撤',fmt(dd,'%')],['累计手续费',fmt(r.fees)]]
  if(['native','research'].includes(mode.value))m.push(['盈利交易日占比',fmt(s.win_rate,'%')],['夏普比率',fmt(s.sharpe_ratio)])
  return m
})
const columns=computed(()=>mode.value==='expiry'?
 [['day','交易日'],['action','事件'],['code','合约'],['qty','手数'],['price','价格'],['premium','权利金'],['variation','盯市'],['fee','费用'],['cash','现金余额']]:
 [['time','时间'],['code','合约'],['direction','方向'],['offset','开平'],['price','成交价'],['volume','数量'],['fee','费用'],['tag','标签']])
</script>
<style scoped>
.run-report{height:100%;overflow:auto;padding:clamp(15px,3vw,40px);box-sizing:border-box;color:var(--wt-text)}
header,.cards{display:flex;gap:20px;align-items:center;justify-content:space-between}header button{margin-left:auto}
h1{margin:.3em 0}h2{font-size:17px;margin-top:24px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(125px,1fr))}
.cards div{border:1px solid var(--wt-line);padding:14px}.cards strong{display:block;font-size:19px;margin-top:7px}p,small{color:var(--wt-muted)}
.chart{height:260px}.drawdown{height:180px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:7px;border-bottom:1px solid var(--wt-line)}button{padding:6px}
</style>
