<template>
  <section class="expiry">
    <h3>跨到期日回测 · 移仓、行权与指派</h3>
    <p>按历史日线回放。自动移仓在指定交易日按两腿开盘价模拟；
      到期按标的期货结算价判断实值。权利金、费用和期货盯市分别记账。</p>
    <p v-if="!packs.length">{{message || '期权工作区还没有跨期日线样本。'}}</p>
    <form v-else-if="pack" @submit.prevent="submit">
      <label>历史样本<select v-model="sample"><option v-for="p in packs" :key="p.id" :value="p.id">{{p.id}} · {{p.start}}—{{p.end}}</option></select></label>
      <label>旧合约<select v-model="old"><option v-for="c in pack.contracts" :key="c.code">{{c.code}}</option></select></label>
      <label>移至合约<select v-model="next"><option v-for="c in successors" :key="c.code">{{c.code}}</option></select></label>
      <label>开仓来源<select v-model="task"><option value="">样本首日开盘</option><option v-for="r in completed" :key="r.id" :value="r.id">{{r.strategy}} · {{r.id}}</option></select></label>
      <label v-if="!task">初始手数（负数为卖方）<input v-model.number="lots" type="number" required></label>
      <label>自动移仓手数<input v-model.number="roll" type="number" min="0" required></label>
      <label>距到期几个交易日移仓<input v-model.number="before" type="number" min="0" required></label>
      <label>初始资金（元）<input v-model.number="capital" type="number" min="1" required></label>
      <label>交易费（元/手/边）<input v-model.number="fee" type="number" min="0" step="0.01" required></label>
      <label>行权或履约费（元/手）<input v-model.number="exfee" type="number" min="0" step="0.01" required></label>
      <label>期货保证金占比 %<input v-model.number="margin" type="number" min=".01" max="100" step=".01" required></label>
      <fieldset><legend>历史行权或配对记录（可选）</legend>
        <div v-for="(r,i) in declarations" :key="i">
          <input type="date" v-model="r.day" required><select v-model="r.code"><option>{{old}}</option><option>{{next}}</option></select>
          <select v-model="r.action"><option value="exercise">买方行权</option><option value="assign">卖方被指派</option>
          <option value="abandon">到期放弃</option><option value="unassigned">到期未被指派</option></select>
          <input type="number" min="1" v-model.number="r.qty" required><button type="button" @click="declarations.splice(i,1)">移除</button>
        </div>
        <button type="button" @click="declarations.push({code:old,day:'',qty:1,action:'exercise'})">添加记录</button>
      </fieldset>
      <button :disabled="busy || !next">运行跨期回测</button>
    </form>
    <small>默认实值空仓按全部被指派回放；有实际配对记录时可覆盖。保证金和费用使用输入值，空头期权保证金以收盘价估算。</small>
    <p role="status">{{message}}</p>
    <label v-if="ids.length">已保存的本账户回测<select v-model="saved" @change="restore"><option value="">选择记录</option><option v-for="i in ids" :key="i">{{i}}</option></select></label>
    <section v-if="report"><router-link :to="{name:'run-report',query:{asset:'option',mode:'expiry',task:saved}}">查看跨期统计报告</router-link><h4>逐笔资金</h4><p>{{report.source}}</p>
      <table><thead><tr><th v-for="s in ['交易日','事件','合约','仓位变动 / 盯市手数','价格','权利金','期货盯市','费用','现金余额']" :key="s">{{s}}</th></tr></thead>
      <tbody><tr v-for="(e,i) in report.events" :key="i"><td v-for="key in ['day','action','code','qty','price','premium','variation','fee','cash']" :key="key">{{e[key] ?? '—'}}</td></tr></tbody></table>
      <h4>日终持仓和资金</h4><table><thead><tr><th v-for="h in ['交易日','期权手数','期货手数','现金余额','期权市值','权益','保证金','可用资金']" :key="h">{{h}}</th></tr></thead>
        <tbody><tr v-for="d in report.daily" :key="d.day"><td>{{d.day}}</td><td>{{positions(d.options)}}</td><td>{{positions(d.futures)}}</td>
        <td v-for="k in ['cash','option_value','equity','margin','available']" :key="k">{{d[k]}}</td></tr></tbody></table>
    </section>
  </section>
</template>
<script setup>
import {computed,onMounted,ref,watch} from 'vue'
const props=defineProps({api:Function,runs:Array})
const packs=ref([]),ids=ref([]),sample=ref(''),old=ref(''),next=ref(''),task=ref(''),saved=ref('')
const lots=ref(2),roll=ref(1),before=ref(1),capital=ref(100000),fee=ref(1),exfee=ref(1),margin=ref(10)
const declarations=ref([]),report=ref(null),message=ref(''),busy=ref(false)
const pack=computed(()=>packs.value.find(p=>p.id===sample.value))
const successors=computed(()=>pack.value?.contracts.filter(c=>c.expiry>pack.value.contracts.find(o=>o.code===old.value)?.expiry && c.code.split('-')[1]===old.value.split('-')[1]) || [])
const completed=computed(()=>props.runs.filter(r=>r.strategy_type==='cta' && r.status==='completed'))
watch(sample,()=>{old.value=pack.value?.contracts[0]?.code || '';report.value=null;declarations.value=[]})
watch([old,successors],()=>{if(!successors.value.some(c=>c.code===next.value))next.value=successors.value[0]?.code || ''})
onMounted(async()=>{
  try{const d=await props.api('expiry-catalog');packs.value=d.samples;ids.value=d.runs;sample.value=packs.value[0]?.id || ''}catch(e){message.value=e.message}
})
function positions(v){return Object.entries(v).map(([k,n])=>k+'：'+n).join('；') || '空仓'}
async function restore(){if(saved.value)try{report.value=(await props.api('expiry-detail',{run:saved.value})).report}catch(e){message.value=e.message}}
async function submit(){
  if(busy.value)return
  busy.value=true;report.value=null;message.value=''
  try{const r=await props.api('expiry-run',{sample:sample.value,old:old.value,new:next.value,lots:lots.value,roll:roll.value,before:before.value,
    capital:capital.value,fee:fee.value,exercise_fee:exfee.value,margin_rate:margin.value/100,
    declarations:declarations.value.map(r=>({...r,day:r.day.replaceAll('-','')})),task:task.value,strategy:completed.value.find(r=>r.id===task.value)?.straid || ''})
    report.value=r.report;ids.value.unshift(r.id);saved.value=r.id
  }catch(e){message.value=e.message}finally{busy.value=false}
}
</script>
<style scoped>.expiry{margin:16px 0;overflow:auto}form{display:flex;flex-wrap:wrap;gap:12px;align-items:end}label{display:grid;gap:4px}th,td{padding:6px;text-align:left}fieldset{width:100%}button,input,select{padding:5px}</style>
