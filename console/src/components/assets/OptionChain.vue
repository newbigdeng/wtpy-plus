<template>
  <section><h3>商品期权合约链</h3>
    <form @submit.prevent="search"><label>标的期货 <input v-model.trim="underlying" placeholder="例如 DCE.m2701" required></label> <button>查看合约链</button></form>
    <p role="status">{{message}}</p>
    <table v-if="contracts.length"><thead><tr><th>方向</th><th>行权价</th><th>到期日</th><th>合约</th></tr></thead>
    <tbody><tr v-for="row in contracts" :key="row.symbol"><td>{{row.side}}</td><td>{{row.strike}}</td><td>{{row.expiry}}</td><td><button @click="$emit('view',row.symbol)">{{row.symbol}}</button></td></tr></tbody></table>
  </section>
</template>
<script setup>
import {ref} from 'vue'
defineEmits(['view'])
const underlying=ref(''),contracts=ref([]),message=ref('')
let serial=0
async function search(){
  const id=++serial;contracts.value=[];message.value='读取合约表…'
  try{
    const response=await fetch('/mgr/assets/option/chain',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({underlying:underlying.value})})
    const data=await response.json()
    if(id!==serial)return
    if(data.result!==0)throw new Error(data.message)
    contracts.value=data.contracts;message.value=data.contracts.length?data.source+' · 点击合约代码开始看盘':'合约表没有该标的的期权'
  }catch(error){if(id===serial)message.value=error.message}
}
</script>
<style scoped>section{margin-top:24px}th,td{text-align:left;padding:5px 10px}input,button{background:var(--wt-surface);color:var(--wt-text);border:1px solid var(--wt-line);padding:6px}button{cursor:pointer}</style>
