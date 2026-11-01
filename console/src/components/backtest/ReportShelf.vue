<template>
  <section class="report-shelf">
    <header><div><small>RESEARCH / REPORTS</small><h1>{{names[asset]}}回测报告</h1></div>
      <el-button :loading="busy" @click="load">刷新列表</el-button></header>
    <p>选择回测记录，查看统计、资金曲线和逐笔成交。</p>
    <div class="filters">
      <label>报告类型<select :value="mode" @change="setSource($event.target.value)">
        <option value="all">全部类型</option><option v-for="id in sources" :key="id" :value="id">{{types[id]}}</option></select></label>
      <label v-if="asset==='futures' && mode==='native'">研究策略<select :value="route.query.strategy || ''" @change="setSource(mode,$event.target.value)">
        <option value="">全部策略</option>
        <option v-for="s in strategies" :key="s.id" :value="s.id">{{s.name}}</option></select></label>
      <label>查找记录<input v-model.trim="search" type="search" placeholder="策略、标的或任务编号"></label>
    </div>
    <p v-if="error" role="alert">{{error}}</p><p v-if="busy" role="status">正在读取报告目录…</p>
    <p v-else-if="!filtered.length && !error">{{search?'没有匹配这次搜索的记录。':'这个账户在当前分类下还没有回测记录。'}}</p>
    <div v-if="filtered.length" class="rows"><p>共 {{filtered.length}} 条记录 · 按启动时间从新到旧</p><table><thead><tr><th>策略 / 任务</th><th>类型</th><th>启动时间</th><th>标的</th><th>状态</th><th>报告</th></tr></thead>
      <tbody><tr v-for="r in filtered" :key="r.mode+r.id"><td>{{r.title}}<small>{{r.id}}</small></td><td>{{types[r.mode]}}</td><td>{{r.at?new Date(r.at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—'}}</td>
        <td>{{r.symbol || '—'}}</td><td>{{states[r.status] || r.status || '历史记录'}}</td>
        <td><router-link :to="{name:'run-report',query:{asset,mode:r.mode,task:r.id,strategy:r.straid,listMode:route.query.mode,listStrategy:route.query.strategy,q:route.query.q}}">查看报告</router-link></td></tr></tbody></table></div>
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
const mode=computed(()=>sources.value.includes(route.query.mode)?route.query.mode:'all')
const strategies=ref([]),rows=ref([]),busy=ref(false),error=ref('')
const search=computed({
  get:()=>String(route.query.q||''),
  set:q=>router.replace({name:'backtest-reports',query:{...route.query,q:q||undefined}})
})
const filtered=computed(()=>rows.value.filter(r=>[r.title,r.symbol,r.id].join(' ').toLowerCase().includes(search.value.toLowerCase())))
function setSource(mode,strategy){router.push({name:'backtest-reports',query:{asset:asset.value,mode:mode==='all'?undefined:mode,strategy:strategy||undefined,q:route.query.q}})}
async function post(path,body={}){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  const d=await r.json();if(!r.ok||d.result!==0)throw Error(d.message||'无法读取报告目录');return d
}
let generation=0
async function load(){
  const token=++generation,a=asset.value,modes=mode.value==='all'?sources.value:[mode.value],pick=route.query.strategy
  rows.value=[];error.value='';busy.value=true
  const results=await Promise.allSettled(modes.map(async m=>{
    let list
    if(a==='futures' && m==='native'){
      const {strategies:ss}=await post('/bt/qrystras')
      if(token===generation)strategies.value=ss
      list=(await Promise.all(ss.filter(s=>!pick || pick===s.id).map(async s=>
        (await post('/bt/qrystrabts',{straid:s.id})).backtests.map(r=>({...r,title:s.name,straid:s.id}))))).flat()
    }else if(m==='plus'){
      list=(await post('/bt/plus/list')).tasks.map(r=>({...r,title:r.label||r.straid}))
    }else if(m==='research'){
      const {experiments,projects}=await post('/mgr/research/list',{include_catalog:false})
      list=experiments.map(r=>({...r,title:projects.find(p=>p.id===r.project_id)?.title||r.project_id}))
    }else if(m==='expiry'){
      list=(await post('/mgr/assets/option/expiry-catalog')).runs.map(id=>({id,title:'移仓与到期清算',status:'completed',runtime:id.replace(/^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)-.*$/,'$1.$2.$3 $4:$5:$6')}))
    }else{
      list=(await post('/mgr/assets/'+a+'/runs')).runs.sort((x,y)=>String(y.runtime).localeCompare(String(x.runtime))).map(r=>({...r,title:r.strategy}))
    }
    const batch=list.map(r=>({...r,mode:m,at:r.created_at?Number(r.created_at)*1000:Date.parse(String(r.runtime).replace(/\./g,'-').replace(' ','T')+'+08:00'),symbol:r.dataset?.code || r.code || ''}))
    if(token===generation)rows.value=[...rows.value,...batch].sort((x,y)=>(y.at||0)-(x.at||0))
  }))
  if(token!==generation)return
  error.value=results.flatMap((v,i)=>v.status==='rejected'?[types[modes[i]]+'：'+v.reason.message]:[]).join('；');busy.value=false
}
watch([asset,mode,()=>route.query.strategy],load,{immediate:true})
</script>
<style scoped>
.report-shelf{padding:clamp(15px,3vw,40px);color:var(--wt-text)}header,.filters{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header{justify-content:space-between}h1{margin:.3em 0}p,small{color:var(--wt-muted)}.filters{margin:20px 0}
label{display:grid;gap:5px}select,input{padding:7px;color:inherit;background:var(--wt-surface);border:1px solid var(--wt-line)}
.rows{overflow-x:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px;border-bottom:1px solid var(--wt-line)}td small{display:block;margin-top:3px}a{color:var(--wt-text)}
</style>
