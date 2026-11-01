<template>
  <section class="asset-research">
    <h2>{{backtest?'原生策略回测':'本地历史数据'}} · {{name}}</h2>
    <button @click="load">刷新</button><p role="status">{{message}}</p>
    <template v-if="backtest">
      <form @submit.prevent="run">
        <label>策略版本<select v-model="selected"><option v-for="s in strategies" :key="s.id" :value="s">{{s.name}} · v{{s.version}}</option></select></label>
        <label>行情数据<select v-model="dataset"><option v-for="d in datasets" :key="d.id" :value="d.id">{{d.label}}</option></select></label>
        <label v-if="asset==='option'">附加合约（可多选）<select v-model="companions" multiple><option v-for="d in datasets.filter(r=>r.id!==dataset)" :key="d.id" :value="d.id">{{d.label}}</option></select></label>
        <label>开始<input type="date" v-model="start" required></label><label>结束<input type="date" v-model="end" required></label>
        <label>初始资金<input type="number" min="1" step="any" v-model.number="capital" required></label>
        <button :disabled="busy || !selected || !dataset">运行所选策略</button>
      </form>
      <p v-if="asset==='option'">按权利金行情回放。请选择到期日前的日期。行权、指派和卖方保证金未建模。</p>
      <h3>我的回测记录</h3><p v-if="!runs.length">此资产下还没有任务。</p>
      <table v-else><thead><tr><th>策略</th><th>合约</th><th>启动时间</th><th>状态</th><th>任务</th></tr></thead>
      <tbody><tr v-for="r in runs" :key="r.id"><td>{{r.strategy}}</td><td>{{r.dataset?.code}}</td><td>{{r.runtime}}</td><td>{{r.status}}</td><td><button @click="readDetail(r)">查看结果</button></td></tr></tbody></table>
      <section v-if="detail"><h3>任务结果 · {{detail.status}}</h3><p v-if="detail.error">{{detail.error}}</p>
        <dl><template v-for="(value,key) in detail.summary" :key="key"><dt>{{key}}</dt><dd>{{value}}</dd></template></dl>
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
import {ref,onMounted,watch} from 'vue'
const props=defineProps({asset:String,name:String,backtest:Boolean})
const detail=ref(null)
const datasets=ref([]),strategies=ref([]),runs=ref([]),selected=ref(null),dataset=ref('')
const start=ref(''),end=ref(''),capital=ref(100000),message=ref(''),busy=ref(false)
const companions=ref([])
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
  try{detail.value=(await api('detail',{strategy:row.straid,task:row.id})).detail}catch(error){message.value=error.message}
}
async function run(){
  if(busy.value)return
  busy.value=true
  try{
    await api('run',{library_id:selected.value.id,version:selected.value.version,dataset:dataset.value,companions:companions.value,start:start.value,end:end.value,capital:capital.value})
    await load()
  }catch(error){message.value=error.message}finally{busy.value=false}
}
onMounted(load)
</script>
<style scoped>.asset-research{padding:22px;overflow:auto;color:var(--wt-text)}form{display:flex;gap:12px;flex-wrap:wrap;align-items:end}label{display:grid;gap:5px}button,input,select{padding:6px;background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line)}button{cursor:pointer}th,td{text-align:left;padding:7px}p{color:var(--wt-muted)}</style>
