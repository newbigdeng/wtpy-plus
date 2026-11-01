<template>
  <section class="asset-shortcuts">
    <template v-if="asset==='crypto'">
      <strong>热门币对</strong>
      <button v-for="[code,name] in coins" :key="code" type="button" @click="$emit('pick',code)">{{name}} · {{code}}</button>
    </template>
    <template v-else>
      <strong>已订阅期货的期权</strong>
      <label v-if="groups.length">组合 <select v-model="selectedGroup"><option v-for="g in groups" :key="g.id" :value="g.id">{{g.name || g.id}}</option></select></label>
      <span>{{message}}</span>
      <button v-for="row in rows" :key="row.future" type="button" :disabled="!row.underlying" @click="$emit('pick',row.underlying)">
        {{row.name}} · {{row.future}}{{!row.underlying?'（无在期链）':row.future!==row.underlying?' → '+row.underlying:''}}
      </button>
    </template>
  </section>
</template>
<script setup>
import {onMounted,ref,watch} from 'vue'
const props=defineProps({asset:String,group:String})
defineEmits(['pick'])
const coins=[['BTC-USD','比特币'],['ETH-USD','以太坊'],['SOL-USD','Solana'],['XRP-USD','XRP'],['DOGE-USD','狗狗币']]
const groups=ref([]),selectedGroup=ref(''),rows=ref([]),message=ref('')
let serial=0
async function post(path,body){
  const result=await (await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()
  if(result.result!==0)throw new Error(result.message || '未能读取订阅')
  return result
}
onMounted(async()=>{
  if(props.asset!=='option')return
  try{
    const data=await post('/mgr/qrygrp',{})
    groups.value=data.groups || []
    selectedGroup.value=groups.value.find(g=>g.id===props.group)?.id || groups.value.find(g=>g.running)?.id || groups.value[0]?.id || ''
    if(!selectedGroup.value)message.value='没有可读取的组合'
  }catch(e){message.value=e.message}
})
watch(selectedGroup,async id=>{
  const seq=++serial
  rows.value=[];message.value='正在读取组合订阅…'
  try{
    const data=await post('/mgr/assets/option/suggestions',{groupid:id})
    if(seq===serial){rows.value=data.chains;message.value=rows.value.length?data.profile:'该组合还没有期货订阅'}
  }catch(e){if(seq===serial)message.value=e.message}
})
</script>
<style scoped>
.asset-shortcuts{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0;color:var(--wt-text)}
span{font-size:12px;color:var(--wt-muted)}button,select{padding:5px 8px;background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line);border-radius:4px}button{cursor:pointer}button:disabled{opacity:.55}
</style>
