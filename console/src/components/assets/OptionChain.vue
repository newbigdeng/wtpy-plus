<template>
  <section><h3>商品期权合约链</h3>
    <form @submit.prevent="search"><label>标的期货 <input v-model.trim="underlying" placeholder="例如 DCE.m2701" required></label> <button>查看合约链</button></form>
    <p role="status">{{message}}</p>
    <button v-if="contracts.length" type="button" @click="addShown">选择本页合约</button>
    <table v-if="contracts.length"><thead><tr><th>分析</th><th>方向</th><th>行权价</th><th>到期日</th><th>合约</th></tr></thead>
    <tbody><tr v-for="row in contracts" :key="row.symbol"><td><input type="checkbox" :aria-label="'分析 '+row.symbol" :checked="selected.some(s=>s.symbol===row.symbol)" @change="toggle(row)"></td><td>{{row.side}}</td><td>{{row.strike}}</td><td>{{row.expiry}}</td><td><button @click="$emit('view',row.symbol)">{{row.symbol}}</button></td></tr></tbody></table>
    <OptionAnalysis ref="desk" :selected="selected" :mode="mode" @clear="selected=[]" @analyze="$emit('analyze')" @remove="symbol=>selected=selected.filter(s=>s.symbol!==symbol)"/>
  </section>
</template>
<script setup>
import {ref} from 'vue'
import OptionAnalysis from './OptionAnalysis.vue'
defineProps({mode:String})
defineEmits(['view','analyze'])
const selected=ref([]),desk=ref(null),listed=ref('')
function toggle(row){
  if(selected.value.some(s=>s.symbol===row.symbol))selected.value=selected.value.filter(s=>s.symbol!==row.symbol)
  else if(selected.value.length<96)selected.value=[...selected.value,{...row,underlying:listed.value}]
  else message.value='一次最多选择 96 份期权'
}
function addShown(){
  const additions=contracts.value.filter(r=>!selected.value.some(s=>s.symbol===r.symbol))
  if(selected.value.length+additions.length>96){message.value='请选择部分合约，一次最多 96 份';return}
  selected.value=[...selected.value,...additions.map(r=>({...r,underlying:listed.value}))]
}
defineExpose({stop:()=>desk.value?.stop()})
const underlying=ref(''),contracts=ref([]),message=ref('')
let serial=0
async function search(){
  const id=++serial,requested=underlying.value;contracts.value=[];message.value='读取合约表…'
  try{
    const response=await fetch('/mgr/assets/option/chain',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({underlying:requested})})
    const data=await response.json()
    if(id!==serial)return
    if(data.result!==0)throw new Error(data.message)
    listed.value=requested;contracts.value=data.contracts;message.value=data.contracts.length?data.source+' · 点击合约代码开始看盘':'合约表没有该标的的期权'
  }catch(error){if(id===serial)message.value=error.message}
}
</script>
<style scoped>section{margin-top:24px}th,td{text-align:left;padding:5px 10px}input,button{background:var(--wt-surface);color:var(--wt-text);border:1px solid var(--wt-line);padding:6px}button{cursor:pointer}</style>
