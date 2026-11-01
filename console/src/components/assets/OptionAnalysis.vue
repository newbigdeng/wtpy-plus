<template>
  <section class="analysis">
    <h3>自选期权分析</h3>
    <p>勾选期权后可继续查其他月份或品种。启动时，每份期权只连同它的标的期货订阅。</p>
    <details v-if="selected.length"><summary>已选 {{selected.length}} 份期权 · {{chains.length}} 个标的</summary>
      <button v-for="s in selected" :key="s.symbol" @click="$emit('remove',s.symbol)" :title="'移除 '+s.symbol">{{s.symbol}} ×</button>
      <button @click="$emit('clear')">清空</button>
    </details>
    <form @submit.prevent="start">
      <label>估值模型<select v-model="model"><option value="american">美式期货期权（BAW 近似）</option><option value="black76">欧洲式 Black-76 参考</option></select></label>
      <label>年利率 %<input v-model.number="rate" required type="number" min="0" max="50" step="any"></label>
      <label>独立估值波动率 %<input v-model.number="scenarioVol" type="number" min="0.01" max="500" step="any" placeholder="可选，自行假设"></label>
      <button :disabled="!selected.length">开始分析</button><button v-if="running" type="button" @click="stop">停止分析</button>
    </form>
    <details><summary>曲面查询与 Kirk 跨品种价差</summary>
      <div class="inputs"><label>插值行权价<input v-model.number="strike" type="number" min="0" step="any"></label><label>查询日期<input v-model="expiry" type="date"></label></div>
      <div class="inputs"><label>第一腿 IV<select v-model="first"><option value="">不计算价差</option><option v-for="s in selected" :key="s.symbol">{{s.symbol}}</option></select></label>
        <label>第二腿 IV<select v-model="second"><option value="">选择合约</option><option v-for="s in selected" :key="s.symbol">{{s.symbol}}</option></select></label>
        <label>第二腿单位换算倍数<input v-model.number="ratio" type="number" min="0" step="any"></label>
        <label>价差行权价<input v-model.number="spreadStrike" type="number" step="any"></label>
        <label>两腿相关系数<input v-model.number="rho" type="number" min="-1" max="1" step="any"></label></div>
      <p>Kirk 估算看涨价差期权。到期取两腿较近日期；两腿 IV 取所选合约。换算后价格须同单位。</p>
    </details>
    <p role="status">{{status}}</p>
    <div v-if="frame">
      <p>源端交易日 {{frame.trading_day || '等待行情'}} · {{frame.mode==='simulation'?'7×24 仿真':'交易时段'}} · 有效 {{frame.rows.filter(r=>r.iv!=null).length}} / {{frame.rows.length}}</p>
      <p>使用同步的双边盘口中间价。Theta 按日，Vega 和 Rho 按一个百分点；其余量按一单位标的计算。</p>
      <div class="scroll"><table><thead><tr><th>合约</th><th>试算手数</th><th>期权中价</th><th>期货中价</th><th>IV</th><th>Delta</th><th>Gamma</th><th>Theta</th><th>Vega</th><th>Rho</th><th>源端时间 / 状态</th></tr></thead>
        <tbody><tr v-for="r in frame.rows" :key="r.symbol"><td>{{r.symbol}}</td><td><input class="lots" type="number" step="1" v-model.number="lots[r.symbol]" :aria-label="r.symbol+' 试算手数'" placeholder="0"></td><td>{{n(r.premium)}}</td><td>{{n(r.forward)}}</td><td>{{pct(r.iv)}}</td>
          <td>{{n(r.delta,4)}}</td><td>{{n(r.gamma,6)}}</td><td>{{n(r.theta,4)}}</td><td>{{n(r.vega,4)}}</td><td>{{n(r.rho,4)}}</td><td>{{r.reason || r.source_time}}</td></tr></tbody></table></div>
      <div v-if="scenarioVol" class="scroll"><h4>按假设波动率估价</h4>
        <p>买入比较卖一价，卖出比较买一价。差额按报价单位显示，未扣手续费。</p>
        <table><thead><tr><th>合约</th><th>假设价格</th><th>假设价格 − 卖一</th><th>买一 − 假设价格</th></tr></thead>
          <tbody><tr v-for="r in frame.rows" :key="r.symbol"><td>{{r.symbol}}</td><td>{{n(r.scenario_price)}}</td><td>{{n(r.scenario_price==null?null:r.scenario_price-r.ask)}}</td><td>{{n(r.scenario_price==null?null:r.bid-r.scenario_price)}}</td></tr></tbody></table></div>
      <div v-if="risk.length"><h4>按标的试算敞口</h4><p>多头用正手数，空头用负手数。每项按合约乘数折算。</p>
        <div v-for="r in risk" :key="r.underlying">{{r.underlying}} · <span v-if="r.missing">等待有效报价、整数手数及合约乘数</span>
          <span v-else>Delta {{n(r.delta)}} · Gamma {{n(r.gamma,4)}} · Theta {{n(r.theta)}} · Vega {{n(r.vega)}} · Rho {{n(r.rho)}}</span></div></div>
      <h4>波动率微笑与期限结构</h4>
      <div v-for="(summary,key) in frame.summaries" :key="key" class="slice">
        <strong>{{key}}</strong>
        <OptionCurve :points="frame.rows.filter(r=>r.underlying===key && r.iv!=null)" />
        <small>平值 Call / Put {{pct(summary.call_atm)}} / {{pct(summary.put_atm)}} · 平值差 {{pp(summary.atm_gap)}} · Vega 加权差 {{pp(summary.weighted_gap)}} · 25Δ 差 {{pp(summary.gap_25d)}} · 10Δ 差 {{pp(summary.gap_10d)}}</small>
        <OptionCurve v-if="summary.history?.length" :history="summary.history"/>
        <p>已完成 {{summary.prior_minutes || 0}} 个分钟样本 · 当前加权差 Z 分数 {{n(summary.zscore)}}</p>
      </div>
      <div v-for="(sides,product) in frame.surfaces" :key="product">
        <h4>{{product}} 波动率曲面</h4>
        <OptionCurve v-if="terms(product).length>1" :terms="terms(product)"/>
        <div v-for="(surface,side) in sides" :key="side"><strong>{{side}}</strong> <small v-if="surface.message">{{surface.message}}</small>
          <div v-else class="scroll"><table><thead><tr><th>行权价 / 到期</th><th v-for="d in surface.expiries" :key="d">{{d}}</th></tr></thead>
            <tbody><tr v-for="(k,i) in surface.strikes" :key="k"><th>{{k}}</th><td v-for="(v,j) in surface.iv[i]" :key="j">{{pct(v)}}</td></tr></tbody></table>
            <p v-if="expiry">查询点 {{pct(surface.query_iv)}}（仅在共同采样范围内插值）</p></div>
        </div>
      </div>
      <p v-if="frame.spread"><strong>Kirk 价差期权</strong> {{frame.spread.reason || ('估值 '+n(frame.spread.price,4)+' · 期限 '+frame.spread.days+' 天 · 换算后期货价差 '+n(frame.spread.forward_spread))}}</p>
      <p>策略只读会话 <code>{{frame.session}}</code></p>
    </div>
  </section>
</template>
<script setup>
import {computed,onMounted,onBeforeUnmount,ref,watch} from 'vue'
import OptionCurve from './OptionCurve.vue'
const props=defineProps({selected:Array,mode:String})
const emit=defineEmits(['clear','remove','analyze'])
const model=ref('american'),rate=ref(2),strike=ref(null),expiry=ref(''),first=ref(''),second=ref('')
const scenarioVol=ref('')
const ratio=ref(1),rho=ref(0),spreadStrike=ref(0)
const frame=ref(null),running=ref(false),status=ref('')
const chains=computed(()=>[...new Set(props.selected.map(s=>s.underlying))].map(underlying=>({
  underlying,symbols:props.selected.filter(s=>s.underlying===underlying).map(s=>s.symbol)})))
const lots=ref({})
const risk=computed(()=>{
  const grouped=new Map()
  for(const row of frame.value?.rows||[]){
    const qty=lots.value[row.symbol]
    if(!qty)continue
    const key=row.underlying,r=grouped.get(key)||{underlying:key,delta:0,gamma:0,theta:0,vega:0,rho:0}
    if(!Number.isInteger(qty)||row.iv==null||!(row.multiplier>0))r.missing=true
    else for(const field of ['delta','gamma','theta','vega','rho'])r[field]+=qty*row.multiplier*row[field]
    grouped.set(key,r)
  }
  return [...grouped.values()]
})
const n=(v,d=2)=>v==null?'—':Number(v).toFixed(d)
const pct=v=>v==null?'—':n(v*100)+'%'
const pp=v=>v==null?'—':n(v*100)+' 个百分点'
let controller,resume=false
function stop(){
  controller?.abort();controller=null;running.value=false;resume=false;frame.value=null;status.value=''
}
defineExpose({stop})
watch([()=>props.selected,()=>props.mode,model,rate,scenarioVol,strike,expiry,first,second,ratio,rho,spreadStrike],stop)
function terms(product){
  return Object.entries(frame.value.summaries).flatMap(([key,values])=>{
    const row=frame.value.rows.find(r=>r.underlying===key && r.product===product && r.iv!=null)
    return row?[{...values,expiry:row.expiry}]:[]
  }).sort((a,b)=>a.expiry-b.expiry)
}
async function start(){
  stop()
  if(!props.selected.length || document.hidden)return
  const chosen=new Set(props.selected.map(s=>s.symbol))
  if(first.value && (!chosen.has(first.value) || !chosen.has(second.value) || first.value===second.value)){
    status.value='请选择不同标的的两腿合约';return
  }
  if(Boolean(strike.value)!==Boolean(expiry.value)){status.value='插值需要同时填写日期与行权价';return}
  emit('analyze')
  const c=new AbortController();controller=c;running.value=true;status.value='连接自选行情…'
  try{
    const body={chains:chains.value,mode:props.mode,model:model.value,rate:rate.value/100}
    if(scenarioVol.value!=='')body.scenario_vol=scenarioVol.value/100
    if(expiry.value)body.query={date:Number(expiry.value.replaceAll('-','')),strike:strike.value}
    if(first.value)body.spread={first:first.value,second:second.value,ratio:ratio.value,strike:spreadStrike.value,correlation:rho.value}
    const res=await fetch('/mgr/assets/option/analysis',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:c.signal})
    if(!res.headers.get('content-type')?.includes('ndjson'))throw new Error((await res.json()).message || '分析参数无效')
    const reader=res.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffer=''
    while(controller===c){
      const {done,value}=await reader.read()
      if(done)throw new Error('分析会话已关闭')
      if(controller!==c)return
      buffer+=value
      const lines=buffer.split('\n');buffer=lines.pop()
      for(const line of lines.filter(Boolean)){
        const event=JSON.parse(line)
        if(event.error)throw new Error(event.error)
        if(event.status)status.value=event.status
        if(event.session)status.value='已请求 '+event.subscribed+' 个合约（含标的期货）'
        if(event.frame)frame.value=event.frame
      }
    }
  }catch(error){if(controller===c){stop();status.value=error.message}}
}
function visibility(){
  if(document.hidden && running.value){stop();resume=true;status.value='页面已隐藏，行情已暂停'}
  else if(!document.hidden && resume)start()
}
onMounted(()=>document.addEventListener('visibilitychange',visibility))
onBeforeUnmount(()=>{stop();document.removeEventListener('visibilitychange',visibility)})
</script>
<style scoped>
.analysis{margin-top:20px;border-top:1px solid var(--wt-line);padding-bottom:24px}
form,.inputs{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin:12px 0}label{display:grid;gap:4px}
button,input,select{background:var(--wt-surface);color:var(--wt-text);border:1px solid var(--wt-line);padding:6px}button,summary{cursor:pointer}
small,p{color:var(--wt-muted);font-size:12px}.scroll{overflow:auto}table{border-collapse:collapse}td,th{white-space:nowrap;padding:5px 8px;border-bottom:1px solid var(--wt-line);font-variant-numeric:tabular-nums}
.lots{width:64px}details{margin:10px 0}.slice{border-left:2px solid var(--wt-line);padding-left:12px}code{user-select:all}
</style>
