<template>
  <section class="hub">
    <div class="hub-row"><strong>{{title}}数据中台</strong><span>{{labels[info.state] || '读取中'}}</span>
      <small>{{info.symbols?.length || 0}} 个已保存订阅</small>
      <button @click="opened=!opened">{{opened?'收起':'选择标的'}}</button>
      <button v-if="info.enabled && canManage" :disabled="busy" @click="change('stop')">停止采集</button>
    </div>
    <p v-if="error || info.message" role="alert">{{error || info.message}}</p>
    <div v-if="opened" class="edit">
      <AssetShortcuts :asset="asset" :group="$route.query.group" @pick="pick"/>
      <template v-if="asset==='option'">
        <form @submit.prevent="loadChain(future)"><label>标的期货 <input v-model.trim="future" placeholder="例如 DCE.m2701" required></label> <button>查看合约链</button></form>
        <label v-if="chain.length">可采合约 <select v-model="option"><option v-for="r in chain" :key="r.symbol" :value="r.symbol">{{r.side}} {{r.strike}} · {{r.symbol}}</option></select>
          <button :disabled="disabled" @click="add(option)">加入列表</button></label>
      </template>
      <form @submit.prevent="add(input);input=''"><input v-model.trim="input" :placeholder="asset==='option'?'输入完整期权代码':'输入现货币对'" required>
        <button :disabled="disabled">加入列表</button></form>
      <p>待采集 <span v-if="!draft.length">尚未选择标的</span>
        <button v-for="s in draft" :key="s" :disabled="disabled" :aria-label="'移除 '+s" @click="draft=draft.filter(x=>x!==s)">{{s}} ×</button></p>
      <button :disabled="disabled || !dirty" @click="change('save')">仅保存订阅</button>
      <button :disabled="disabled || !draft.length || info.state==='offline'" @click="change('start')">启动采集</button>
      <small>{{info.enabled?'修改订阅前请先停止采集。':'保存后需手动启动，关掉页面后仍继续采集。'}}</small>
    </div>
  </section>
</template>
<script setup>
import {computed,onMounted,onBeforeUnmount,ref} from 'vue'
import AssetShortcuts from '../assets/AssetShortcuts.vue'
import {useSessionStore} from '@/store.js'
const props=defineProps({asset:String,title:String,editing:Boolean})
const info=ref({}),draft=ref([]),input=ref(''),future=ref(''),chain=ref([]),option=ref('')
const opened=ref(props.editing),busy=ref(false),error=ref('')
const dirty=computed(()=>JSON.stringify(draft.value)!==JSON.stringify(info.value.symbols || []))
const session=useSessionStore()
const canManage=computed(()=>['admin','superman'].includes(session.cache.userinfo?.role))
const disabled=computed(()=>!canManage.value || busy.value || info.value.enabled || info.value.revision==null)
const labels={running:'运行中',stopped:'已停止',offline:'管理服务离线',pending:'正在应用',failed:'启动失败'}
let timer,version=0,chainVersion=0
function add(code){if(!disabled.value && code && !draft.value.includes(code))draft.value=[...draft.value,code]}
function pick(code){if(props.asset==='option')loadChain(code);else add(code)}
async function post(url,data){
  const result=await (await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})).json()
  if(result.result!==0)throw new Error(result.message || '请求失败')
  return result
}
async function loadChain(code){
  const id=++chainVersion
  future.value=code;chain.value=[];error.value=''
  try{
    const result=await post('/mgr/assets/option/chain',{underlying:code})
    if(id!==chainVersion)return
    chain.value=result.contracts;option.value=chain.value[0]?.symbol || ''
    if(!chain.value.length)error.value='该标的没有期权链'
  }catch(e){if(id===chainVersion)error.value=e.message}
}
async function refresh(){
  if(busy.value || document.hidden)return
  const id=++version
  try{
    const result=await post('/mgr/assets/'+props.asset+'/collector',{action:'status'})
    if(id!==version)return
    if(info.value.revision==null || result.revision!==info.value.revision)draft.value=[...result.symbols]
    info.value=result
  }catch(e){if(id===version)error.value=e.message}
}
async function change(action){
  ++version;busy.value=true;error.value=''
  try{
    const result=await post('/mgr/assets/'+props.asset+'/collector',{action,symbols:draft.value,revision:info.value.revision})
    info.value=result;draft.value=[...result.symbols]
  }catch(e){error.value=e.message}
  finally{busy.value=false;refresh()}
}
onMounted(()=>{refresh();timer=setInterval(refresh,4000)})
onBeforeUnmount(()=>{++version;++chainVersion;clearInterval(timer)})
</script>
<style scoped>
.hub{margin-top:8px;border:1px solid var(--wt-line);border-radius:4px;color:var(--wt-text);padding:10px}
.hub-row,.edit form,.edit>label{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hub-row strong{min-width:145px}.edit{border-top:1px solid var(--wt-line);margin-top:8px}.edit form,.edit>label{margin:8px 0}
button,input,select{background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line);padding:5px 8px;border-radius:4px}button{cursor:pointer}button:disabled{opacity:.55}small{color:var(--wt-muted);margin-left:8px}
</style>
