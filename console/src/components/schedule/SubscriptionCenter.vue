<template>
  <section class="center" aria-label="数据中台订阅">
    <nav aria-label="采集资产"><button v-for="(title,key) in names" :key="key" :aria-pressed="asset===key" @click="choose(key)">{{title}}</button></nav>
    <template v-if="asset==='futures'">
      <label>组合 <select :value="group" @change="selectGroup($event.target.value)"><option v-for="g in groups" :key="g.id" :value="g.id">{{g.name || g.id}}</option></select></label>
      <label>期货数据中台 <select v-model="profile"><option v-for="p in profiles" :key="p.id" :value="p.id">{{p.label}}</option></select></label>
      <p v-if="message" role="status">{{message}}</p>
      <FuturesSubscriptions v-if="profile" :key="group+'/'+profile" :groupid="group" :profileid="profile"/>
    </template>
    <AssetDatahub v-else :key="asset" :asset="asset" :title="names[asset]" editing/>
  </section>
</template>
<script setup>
import {computed,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import FuturesSubscriptions from './FuturesSubscriptions.vue'
import AssetDatahub from './AssetDatahub.vue'
const names={futures:'期货',option:'商品期权',crypto:'加密货币'}
const route=useRoute(), router=useRouter()
const asset=computed(()=>Object.hasOwn(names,route.query.asset)?route.query.asset:'futures')
const groups=ref([]),group=ref(''),profiles=ref([]),profile=ref(''),message=ref('')
let serial=0
function choose(key){router.push({path:'/schedule',query:{tab:'subscriptions',asset:key,group:group.value || route.query.group}})}
function selectGroup(id){router.replace({path:'/schedule',query:{tab:'subscriptions',asset:'futures',group:id}})}
async function post(path,body){
  const data=await (await fetch('/mgr/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()
  if(data.result!==0)throw new Error(data.message || '无法读取期货中台')
  return data
}
async function load(){
  const seq=++serial
  profile.value='';profiles.value=[];message.value=''
  if(asset.value!=='futures')return
  try{
    const {groups:available}=await post('qrygrp',{})
    if(seq!==serial)return
    groups.value=available
    const wanted=route.query.group
    const candidates=wanted?available.filter(g=>g.id===wanted):[...available].sort((a,b)=>Number(!!b.running)-Number(!!a.running))
    if(!candidates.length)throw new Error('没有可读取的组合')
    for(const g of candidates){
      const data=await post('qrychannels',{groupid:g.id})
      if(seq!==serial)return
      let id=data.current || data.profiles[0]?.id
      if(route.query.hub){
        id=null
        for(const p of data.profiles){
          const snapshot=await post('market/subscriptions',{groupid:g.id,profileid:p.id})
          if(snapshot.datahub_id===route.query.hub){id=p.id;break}
        }
      }
      if(seq!==serial)return
      if(id){group.value=g.id;profiles.value=data.profiles;profile.value=id;return}
    }
    message.value='当前组合没有登记所选期货数据中台'
  }catch(e){if(seq===serial)message.value=e.message}
}
watch(()=>[asset.value,route.query.group,route.query.hub],load,{immediate:true})
</script>
<style scoped>
.center{padding:16px;min-width:0}.center h2{font-size:18px}.center nav,.center>label{display:inline-flex;align-items:center;gap:8px;margin:0 12px 12px 0}.center nav{display:flex;flex-wrap:wrap}
button,select{color:var(--wt-text);background:var(--wt-surface);border:1px solid var(--wt-line);padding:6px 10px;border-radius:4px}button{cursor:pointer}button[aria-pressed=true]{font-weight:bold;border-color:currentColor}
</style>
