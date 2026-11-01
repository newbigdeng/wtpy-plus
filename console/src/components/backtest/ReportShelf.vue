<template>
  <section class="report-shelf">
    <header><div><small>RESEARCH / REPORTS</small><h1>{{names[asset]}}回测报告</h1></div>
      <el-button :loading="busy" @click="load">刷新列表</el-button></header>
    <p>选择回测记录，查看统计、资金曲线和逐笔成交。</p>
    <div class="filters">
      <label>报告类型<select :value="mode" @change="setSource($event.target.value)">
        <option v-for="id in sources" :key="id" :value="id">{{types[id]}}</option></select></label>
      <label v-if="asset==='futures' && mode==='native'">研究策略<select :value="strategy" @change="setSource(mode,$event.target.value)">
        <option v-for="s in strategies" :key="s.id" :value="s.id">{{s.name}}</option></select></label>
      <label>查找记录<input v-model.trim="search" type="search" placeholder="策略、标的或任务编号"></label>
    </div>
    <p v-if="error" role="alert">{{error}}</p><p v-else-if="busy" role="status">正在读取报告目录…</p>
    <p v-else-if="!filtered.length">{{search?'没有匹配这次搜索的记录。':'这个账户在当前分类下还没有回测记录。'}}</p>
    <div v-else class="rows"><table><thead><tr><th>策略 / 任务</th><th>标的</th><th>状态</th><th>报告</th></tr></thead>
      <tbody><tr v-for="r in filtered" :key="r.id"><td>{{r.title}}<small>{{r.id}}</small></td>
        <td>{{r.symbol || '—'}}</td><td>{{states[r.status] || r.status || '历史记录'}}</td>
        <td><router-link :to="{name:'run-report',query:{asset,mode,task:r.id,strategy:r.straid}}">查看报告</router-link></td></tr></tbody></table></div>
  </section>
</template>
<script setup>
import {ref,computed,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
const names={futures:'期货',stock:'股票',option:'商品期权',crypto:'加密货币'}
const types={native:'原生策略',plus:'Plus 仿真',research:'研究实验',expiry:'跨期清算'}
const states={completed:'已完成',running:'运行中',failed:'失败',interrupted:'已中断',queued:'排队中',stopped:'已停止'}
const route=useRoute(),router=useRouter(),asset=computed(()=>Object.hasOwn(names,route.query.asset)?route.query.asset:'futures')
const sources=computed(()=>asset.value==='futures'?['native','plus','research']:asset.value==='option'?['native','expiry']:['native'])
const mode=computed(()=>sources.value.includes(route.query.mode)?route.query.mode:'native')
const strategies=ref([]),strategy=ref(''),rows=ref([]),busy=ref(false),error=ref(''),search=ref('')
const filtered=computed(()=>rows.value.filter(r=>[r.title,r.symbol,r.id].join(' ').toLowerCase().includes(search.value.toLowerCase())))
function setSource(mode,strategy){router.push({name:'backtest-reports',query:{asset:asset.value,mode,strategy}})}
async function post(path,body={}){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  const d=await r.json();if(!r.ok||d.result!==0)throw Error(d.message||'无法读取报告目录');return d
}
let generation=0
async function load(){
  const token=++generation,a=asset.value,m=mode.value
  rows.value=[];error.value='';busy.value=true
  try{
    let list
    if(a==='futures' && m==='native'){
      const {strategies:ss}=await post('/bt/qrystras')
      if(token!==generation)return
      strategies.value=ss;strategy.value=ss.find(s=>s.id===route.query.strategy)?.id || ss[0]?.id || ''
      const selected=ss.find(s=>s.id===strategy.value)
      list=selected?(await post('/bt/qrystrabts',{straid:selected.id})).backtests.map(r=>({...r,title:selected.name,straid:selected.id})).reverse():[]
    }else if(m==='plus'){
      list=(await post('/bt/plus/list')).tasks.map(r=>({...r,title:r.label||r.straid}))
    }else if(m==='research'){
      const {experiments,projects}=await post('/mgr/research/list',{include_catalog:false})
      list=experiments.map(r=>({...r,title:projects.find(p=>p.id===r.project_id)?.title||r.project_id}))
    }else if(m==='expiry'){
      list=(await post('/mgr/assets/option/expiry-catalog')).runs.map(id=>({id,title:'移仓与到期清算',status:'completed'}))
    }else{
      list=(await post('/mgr/assets/'+a+'/runs')).runs.sort((x,y)=>String(y.runtime).localeCompare(String(x.runtime))).map(r=>({...r,title:r.strategy}))
    }
    if(token===generation)rows.value=list.map(r=>({...r,symbol:r.dataset?.code || r.code || ''}))
  }catch(e){if(token===generation)error.value=e.message}finally{if(token===generation)busy.value=false}
}
watch([asset,mode,()=>route.query.strategy],load,{immediate:true})
</script>
<style scoped>
.report-shelf{padding:clamp(15px,3vw,40px);color:var(--wt-text)}header,.filters{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header{justify-content:space-between}h1{margin:.3em 0}p,small{color:var(--wt-muted)}.filters{margin:20px 0}
label{display:grid;gap:5px}select,input{padding:7px;color:inherit;background:var(--wt-surface);border:1px solid var(--wt-line)}
.rows{overflow-x:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px;border-bottom:1px solid var(--wt-line)}td small{display:block;margin-top:3px}a{color:var(--wt-text)}
</style>
