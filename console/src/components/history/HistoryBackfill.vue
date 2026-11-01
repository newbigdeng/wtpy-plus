<template>
 <section class="backfill">
  <template v-if="asset==='futures'">
   <label>目标组合 <select v-model="group"><option v-for="g in groups" :value="g.id" :key="g.id">{{g.name||g.id}}</option></select></label>
   <MarketDataPanel v-if="group" :key="group" :groupid="group"/>
   <p v-else-if="!error">当前没有可访问的期货组合。</p>
  </template>
  <template v-else>
   <h1>{{name}}历史补数</h1><p>选择接口、标的和周期。日线最多 90 天，分钟线最多 3 天；不同接口分别保存，重复提交保留已有记录。</p>
   <form @submit.prevent="submit">
    <label>行情接口<select v-model="provider" required><option v-for="s in sources" :key="s.id" :value="s.id" :disabled="!s.available">{{s.label}}{{s.available?'':' · 未配置'}}</option></select></label>
    <label>周期<select v-model="period"><option v-for="k in supported" :key="k" :value="k">{{labels[k]}}</option></select></label>
    <label>标的代码<input v-model.trim="symbol" :placeholder="examples[asset]" required></label>
    <label>开始日期<input type="date" v-model="start" :max="end" required></label>
    <label>结束日期<input type="date" v-model="end" :min="start" required></label>
    <el-button native-type="submit" type="primary" :disabled="!canWrite||!provider" :loading="busy">补充{{labels[period]}}</el-button>
   </form>
   <p v-if="asset==='crypto'">交易日按 UTC 划分；历史 K 线与采集的逐笔成交分开保存。</p>
   <p v-if="message" role="status">{{message}} <router-link :to="{path:'/history',query:{asset}}">查看历史目录</router-link></p>
   <p v-if="loaded&&!canWrite">当前账户只有查看权限。</p>
  </template>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
 </section>
</template>
<script setup>
import {ref,onMounted,computed,watch} from 'vue'
import {useRoute} from 'vue-router'
import MarketDataPanel from '../monitor/MarketDataPanel.vue'
const props=defineProps({asset:String,name:String}),route=useRoute()
const group=ref(''),groups=ref([]),sources=ref([]),provider=ref(''),symbol=ref(''),canWrite=ref(false),busy=ref(false),error=ref(''),message=ref(''),loaded=ref(false)
const period=ref('d'),labels={d:'日线',m1:'1 分钟线',m5:'5 分钟线'}
const supported=computed(()=>sources.value.find(s=>s.id===provider.value)?.periods||['d'])
watch(supported,v=>{if(!v.includes(period.value))period.value='d'})
watch(period,v=>{if(v!=='d' && (Date.parse(end.value)-Date.parse(start.value))/86400000>=3)start.value=end.value})
const examples={stock:'SSE.600000',option:'DCE.m2701-C-3100',crypto:'BTC-USD'}
const dateStr=n=>new Intl.DateTimeFormat('sv-SE',{timeZone:props.asset==='crypto'?'UTC':'Asia/Shanghai'}).format(Date.now()-n*86400000)
const start=ref(dateStr(8)),end=ref(dateStr(1))
async function request(path,body={}){
 const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 const data=await res.json();if(!res.ok||data.result<0)throw Error(data.message||'无法读取数据')
 return data
}
const path='/mgr/assets/'+props.asset+'/'
onMounted(async()=>{try{
 if(props.asset==='futures'){
  groups.value=(await request('/mgr/qrygrp')).groups||[]
  group.value=groups.value.find(g=>g.id===route.query.group)?.id||groups.value[0]?.id||''
 }else{
  const data=await request(path+'backfill-sources');sources.value=data.sources;canWrite.value=data.can_write;provider.value=sources.value.find(s=>s.available)?.id||''
 }
 loaded.value=true
}catch(e){error.value=e.message}})
async function submit(){if(busy.value)return;busy.value=true;message.value=error.value=''
 try{
  const r=await request(path+'backfill',{provider:provider.value,period:period.value,symbol:symbol.value,start:start.value,end:end.value})
  message.value='返回 '+r.received+' 条，新增 '+r.added+' 条，已有 '+(r.received-r.added)+' 条。实际日期：'+r.first+' 至 '+r.last+'。'
 }catch(e){error.value=e.message}finally{busy.value=false}
}
</script>
<style scoped>
.backfill{height:100%;overflow:auto;box-sizing:border-box;padding:20px clamp(14px,2vw,32px);color:var(--wt-text)}h1{font-size:20px;margin:4px 0}p,label{font-size:13px;color:var(--wt-muted)}form{border-block:1px solid var(--wt-line);margin:20px 0;padding:18px 0;display:flex;align-items:end;flex-wrap:wrap;gap:14px}label{display:inline-grid;gap:5px}input,select{border:1px solid var(--wt-line);color:var(--wt-text);background:var(--wt-surface);padding:6px 9px;border-radius:3px}.el-alert{margin:12px 0}
</style>
