<template><section class="diagnostics">
  <h2>收益与交易质量</h2>
  <p>按 {{sessions}} 个交易日折年，使用日终权益。夏普按零无风险利率计算。共 {{returns.stats.days ?? 0}} 个有效交易日。</p>
  <div class="tiles"><div v-for="[name,value] in perf" :key="name"><small>{{name}}</small><strong>{{value}}</strong></div></div>
  <h3>月度收益</h3><small>每月末权益相对上月末；首月从初始资金起算。</small>
  <div class="scroll" v-if="returns.months.length"><table><thead><tr><th>年份</th><th v-for="m in 12" :key="m">{{m}} 月</th></tr></thead><tbody><tr v-for="y in years" :key="y"><th>{{y}}</th><td v-for="m in 12" :key="m" :style="cell(month(y,m))">{{pct(month(y,m))}}</td></tr></tbody></table></div><p v-else>缺少初始资金或日结权益。</p>
  <p role="status" v-if="busy || error">{{busy?'正在读取该任务的完整账簿…':error}}</p>
  <template v-if="details">
    <template v-if="asset==='futures' || mode==='expiry'"><h2>保证金与资金使用率</h2>
      <template v-if="margin.length"><div class="tiles"><div v-for="[name,val] in capitalUse" :key="name"><small>{{name}}</small><strong>{{val}}</strong></div></div>
      <TerminalChart class="plot" :plot="line(margin,'day',[['已用保证金（元）','margin']])" label="日末保证金"/>
      <TerminalChart class="plot" :plot="line(margin,'day',[['保证金 / 权益（%）','percent']])" label="资金使用率"/></template>
      <p v-else>这个任务没有保证金流水。</p>
      <p v-if="details.per_lot">每平仓一手平均毛收益 {{fmt(details.per_lot.pnl)}} 元，按入场均价和任务保证金率折算每手 {{fmt(details.per_lot.margin)}} 元，收益 / 入场保证金为 {{pct(details.per_lot.ratio)}}。</p>
      <p v-if="details.holding_minutes!=null">每手平均持仓 {{fmt(details.holding_minutes)}} 个自然分钟，含休市时段。</p>
    </template>
    <template v-if="asset==='futures'"><h3>展期与基差</h3>
      <p v-if="!details.roll?.length">没有已标记的移仓成交对。</p>
      <table v-else><thead><tr><th>日期</th><th>旧合约</th><th>新合约</th><th>展期成本（元）</th></tr></thead><tbody><tr v-for="(r,i) in details.roll" :key="i"><td>{{r.day}}</td><td>{{r.old}}</td><td>{{r.new}}</td><td>{{r.cost}}</td></tr></tbody></table>
      <p v-if="details.roll?.length">旧新合约成交价差按持仓方向折算，加两腿费用。累计 {{fmt(rollCost)}} 元。<template v-if="returns.stats.days>=252">按 252 日折年为 {{fmt(rollCost*252/returns.stats.days)}} 元。</template><template v-else>样本不足 252 个交易日，未列年化展期成本。</template>这笔换月差额单列，不再从权益中扣减。</p>
      <template v-for="pair in basisPairs" :key="pair"><h4>{{pair}}</h4><TerminalChart class="plot" :plot="line(details.basis.filter(r=>(r.pair || '基差')===pair),'day',[[pair,'value']])" label="现货基差或跨月价差"/></template>
      <p v-if="!basisPairs.length">任务未保存同日的现货或配对合约价格。</p></template>
    <template v-if="asset==='option'"><h2>期权损益与风险</h2>
      <template v-if="opt">
        <h3>隔夜持仓的盈亏归因（元）</h3>
        <p>使用前一日收盘持仓和 Greeks 分解下一日损益。余项包含日内交易、手续费和模型近似误差。缺少任一持仓腿的同期价格时，该日分解留空。</p>
        <TerminalChart v-if="opt.attribution.length" class="plot" :plot="line(opt.attribution,'day',attr)" label="期权收益归因"/>
        <p>{{contributions}}</p>
        <label>标的 <select v-model="under"><option v-for="key in underlyings" :key="key">{{key}}</option></select></label>
        <label>暴露 <select v-model="greek"><option v-for="g in ['delta','gamma','theta','vega']" :key="g">{{g}}</option></select></label>
        <TerminalChart v-if="exposure.length" class="plot" :plot="line(exposure,'day',[[greek,greek]])" label="持仓希腊字母暴露"/>
        <h3>波动率与流动性</h3><label>期权 <select v-model="leg" aria-label="期权"><option v-for="key in legs" :key="key">{{key}}</option></select></label>
        <TerminalChart v-if="obs.length" class="plot" :plot="line(obs,'day',[['IV（%）','ivPct'],['标的 HV20（%）','hvPct'],['IV 历史分位（%）','rankPct']])" label="历史波动率与分位"/>
        <p>IV 用美式模型、年利率 2%。HV 使用 20 个收盘收益，IV 分位只使用此前至少 20 个有效观测。</p>
        <table v-if="opt.cohorts?.length"><thead><tr><th>入场前 IV 分位</th><th>已平仓记录</th><th>平仓胜率</th><th>平均费前盈亏（元）</th></tr></thead><tbody><tr v-for="r in opt.cohorts" :key="r.label"><td>{{r.label}}</td><td>{{r.count}}</td><td>{{pct(r.win)}}</td><td>{{fmt(r.mean)}}</td></tr></tbody></table>
        <p v-else>没有可按入场前 IV 分位归组的已平仓记录。</p>
        <TerminalChart v-if="obs.length" class="plot" :plot="line(obs,'day',[['成交量（手）','volume'],['持仓（手）','oi']])" label="期权流动性"/>
        <p>本腿有 {{obs.filter(x=>x.spread!=null).length}} 个日末买卖价差观测<span v-if="spread!=null">，平均 {{fmt(spread)}}</span>。</p>
        <template v-for="p in opt.payoffs" :key="p.under+p.expiry"><h3>{{p.under}} · {{p.expiry}} 到期损益</h3><p>{{p.day}} 收盘持仓持有至到期，相对该日市值的变化（元）。不同到期日分别展示。</p>
          <TerminalChart class="plot" :plot="{labels:p.grid,series:[{name:'到期损益（元）',data:p.pnl}]}" label="到期损益"/></template>
        <p v-if="!opt.payoffs.length">没有可完整估值的非空持仓组合。</p>
        <template v-for="pair in [...new Set(opt.spreads.map(s=>s.pair))]" :key="pair"><h3>跨月价差 · {{pair}}</h3><TerminalChart class="plot" :plot="line(opt.spreads.filter(s=>s.pair===pair),'day',[[pair,'value']])" label="跨月价差"/></template>
      </template><p v-else>这个任务缺少冻结的期权腿资料或持仓流水。</p>
    </template>
  </template>
</section></template>
<script setup>
import {computed,ref,watch,onBeforeUnmount} from 'vue'
import TerminalChart from '../terminal/TerminalChart.vue'
import {returnsView} from './reportReturns'
const props=defineProps({asset:String,mode:String,task:String,strategy:String,points:Array,capital:Number,total:Number})
const details=ref(null),error=ref(''),busy=ref(false),leg=ref(''),under=ref(''),greek=ref('delta')
let request
watch(()=>[props.asset,props.mode,props.task,props.strategy],async()=>{
  request?.abort();const ctrl=new AbortController();request=ctrl
  details.value=null;error.value='';busy.value=true
  try{
    const raw=await fetch('/mgr/assets/'+props.asset+'/report-chart',{method:'POST',headers:{'Content-Type':'application/json'},signal:ctrl.signal,
      body:JSON.stringify({mode:props.mode,task:props.task,strategy:props.strategy||'',diagnostics:true})})
    const d=await raw.json();if(d.result!==0)throw Error(d.message)
    if(!ctrl.signal.aborted)details.value=d
  }catch(e){if(!ctrl.signal.aborted)error.value=e.message}finally{if(!ctrl.signal.aborted)busy.value=false}
},{immediate:true})
onBeforeUnmount(()=>request?.abort())
const fmt=n=>Number.isFinite(n)?n.toLocaleString('zh-CN',{maximumFractionDigits:3}):'—'
const pct=n=>Number.isFinite(n)?fmt(n*100)+'%':'—'
const sessions=computed(()=>props.asset==='crypto'?365:252)
const returns=computed(()=>returnsView(details.value?.daily?.map(r=>[r.day,r.equity]) ?? (props.mode==='plus'?[]:props.points),props.capital,sessions.value))
const years=computed(()=>[...new Set(returns.value.months.map(x=>x.key.slice(0,4)))])
const month=(y,m)=>returns.value.months.find(x=>x.key===y+String(m).padStart(2,'0'))?.value
const cell=n=>Number.isFinite(n)?{background:'rgba('+(n>=0?'192,63,62,':'27,133,99,')+Math.min(.5,.08+Math.abs(n)*3)+')'}:{}
const perf=computed(()=>{const s=returns.value.stats,r=details.value?.rounds;return [['年化收益率',pct(s.annual)],['年化波动率',pct(s.volatility)],['夏普',fmt(s.sharpe)],['卡玛（日末）',fmt(s.calmar)],['成交笔数',fmt(details.value?.fills ?? (props.mode==='expiry'?null:props.total))],['已平仓记录',fmt(r?.count)],['平仓胜率（费前）',pct(r?.win_rate)],['平均盈亏比（费前）',fmt(r?.payoff)],['获利因子（费前）',fmt(r?.profit_factor)],['平均持仓（K线根数）',fmt(r?.hold)]]})
const line=(rows,key,fields)=>({labels:rows.map(r=>r[key]),series:fields.map(([name,k])=>({name,data:rows.map(r=>r[k]??null)}))})
const margin=computed(()=>(details.value?.daily||[]).filter(r=>r.margin!=null).map(r=>({...r,percent:r.use==null?null:r.use*100})))
const capitalUse=computed(()=>{const s=details.value,ms=margin.value.map(r=>r.margin),us=margin.value.map(r=>r.use).filter(Number.isFinite);return [['占用峰值（元）',fmt(s.margin_peak??Math.max(...ms))],['占用均值（元）',fmt(s.margin_mean??ms.reduce((a,b)=>a+b,0)/ms.length)],['平均资金使用率',pct(s.utilization??us.reduce((a,b)=>a+b,0)/us.length)],['完整流水最大回撤',pct(s.full_dd??returns.value.stats.drawdown)]]})
const rollCost=computed(()=>(details.value?.roll||[]).reduce((s,r)=>s+Number(r.cost),0))
const basisPairs=computed(()=>[...new Set((details.value?.basis||[]).map(r=>r.pair || '基差'))])
const opt=computed(()=>details.value?.option)
const legs=computed(()=>[...new Set((opt.value?.observations||[]).map(x=>x.code))])
const underlyings=computed(()=>[...new Set((opt.value?.exposure||[]).map(x=>x.under))])
watch(legs,k=>leg.value=k[0]||'');watch(underlyings,k=>under.value=k[0]||'')
const exposure=computed(()=>(opt.value?.exposure||[]).filter(x=>x.under===under.value))
const obs=computed(()=>(opt.value?.observations||[]).filter(x=>x.code===leg.value).map(x=>({...x,ivPct:x.iv==null?null:x.iv*100,hvPct:x.hv==null?null:x.hv*100,rankPct:x.rank==null?null:x.rank*100})))
const spread=computed(()=>{const s=obs.value.filter(x=>x.spread!=null);return s.length?s.reduce((n,x)=>n+x.spread,0)/s.length:null})
const attr=[['账户损益','pnl'],['Delta','delta'],['Gamma','gamma'],['Theta','theta'],['Vega','vega'],['余项','residual']]
const contributions=computed(()=>{const all=opt.value.attribution,ok=all.filter(r=>r.residual!=null),total=ok.reduce((n,r)=>n+r.pnl,0);return '可分解 '+ok.length+' / '+all.length+' 个区间。'+(Math.abs(total)>.005?'占这些区间净损益的比例：'+attr.slice(1).map(([name,k])=>name+' '+pct(ok.reduce((n,r)=>n+r[k],0)/total)).join(' · '):'')})
</script>
<style scoped>
.diagnostics{margin-top:22px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}.tiles div{padding:10px;border:1px solid var(--wt-line);border-radius:5px}.tiles strong{display:block;margin-top:6px}.plot{height:235px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}td,th{padding:7px;text-align:center;white-space:nowrap;border:1px solid var(--wt-line)}p,small{color:var(--wt-muted);font-size:13px}label{margin-right:12px}select{color:inherit;background:var(--wt-surface);border:1px solid var(--wt-line);padding:4px}
</style>
