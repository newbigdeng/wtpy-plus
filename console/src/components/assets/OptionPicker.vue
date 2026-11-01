<template><div class="picker" @focusout="leave">
  <input ref="input" :value="modelValue" :aria-label="label" :placeholder="placeholder" :aria-controls="listId"
    role="combobox" aria-autocomplete="list" :aria-expanded="opened" :aria-activedescendant="opened && items[index]?listId+'-'+index:undefined"
    autocomplete="off" maxlength="42" @focus="start" @input="edit" @keydown="key">
  <div v-if="opened" class="menu">
    <small>{{busy?'读取合约…':ranked?'热门 · '+ranked+' 链按持仓量排序':'在期合约 · 目录匹配'}}</small>
    <ul :id="listId" role="listbox" :aria-label="label+'候选'">
      <li v-for="(item,i) in items" :id="listId+'-'+i" :key="item.code" role="option" :aria-selected="i===index"
        @mousedown.prevent @mouseenter="index=i" @click="choose(item)">
        <b>{{item.code}}</b><span>{{item.label}}{{item.interest!=null?' · 持仓 '+item.interest.toLocaleString()+' 手':''}}</span>
      </li>
    </ul><small v-if="!busy && !items.length">{{error || '没有匹配的在期合约'}}</small>
  </div>
</div></template>
<script setup>
import {onBeforeUnmount,ref,useId} from 'vue'
const props=defineProps({modelValue:String,placeholder:String,label:String,futures:Boolean})
const emit=defineEmits(['update:modelValue','pick'])
const listId=useId(),input=ref(null),items=ref([]),ranked=ref(''),index=ref(0),opened=ref(false),busy=ref(false),error=ref('')
let timer,request,promise
function close(){clearTimeout(timer);request?.abort();opened.value=false;busy.value=false;promise=null}
function leave(e){if(!e.currentTarget.contains(e.relatedTarget))close()}
function load(){
  const prefix=input.value.value
  const ctrl=new AbortController();request?.abort();request=ctrl;busy.value=true
  promise=(async()=>{
    try{
      const r=await fetch('/mgr/assets/option/lookup',{method:'POST',signal:ctrl.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({prefix,futures:props.futures})})
      const data=await r.json()
      if(ctrl.signal.aborted)return
      if(data.result!==0)throw Error(data.message)
      items.value=data.items;ranked.value=data.ranked_chain
    }catch(e){if(!ctrl.signal.aborted)error.value=e.message}
    finally{if(!ctrl.signal.aborted)busy.value=false}
  })()
  return promise
}
function start(){opened.value=true;index.value=0;error.value='';load()}
function edit(e){
  emit('update:modelValue',e.target.value);clearTimeout(timer);request?.abort();promise=null
  items.value=[];ranked.value='';error.value='';index.value=0;opened.value=true;busy.value=true
  timer=setTimeout(load,140)
}
function choose(item){close();emit('update:modelValue',item.code);emit('pick',item.code)}
async function key(e){
  if(e.isComposing || e.shiftKey)return
  if(e.key==='Escape'){close();return}
  if(!opened.value && e.key==='ArrowDown'){e.preventDefault();start();return}
  if(!opened.value)return
  if(['ArrowDown','ArrowUp'].includes(e.key)){
    e.preventDefault();index.value=Math.max(0,Math.min(items.value.length-1,index.value+(e.key==='ArrowDown'?1:-1)))
    document.getElementById(listId+'-'+index.value)?.scrollIntoView({block:'nearest'});return
  }
  if(e.key==='Tab' || e.key==='Enter'){
    if(!busy.value && !items.value.length){close();return}
    e.preventDefault();clearTimeout(timer)
    const before=input.value.value
    if(busy.value)await (promise || load())
    if(!opened.value || before!==input.value.value)return
    if(items.value[index.value])choose(items.value[index.value])
    else if(e.key==='Enter' && before.trim()){close();emit('pick',before.trim())}
  }
}
onBeforeUnmount(close)
</script>
<style scoped>
.picker{position:relative;display:inline-block}input{box-sizing:border-box;width:min(248px,68vw);border:1px solid var(--wt-line);border-radius:4px;padding:8px 10px;color:var(--wt-text);background:var(--wt-surface)}
.menu{position:absolute;left:0;top:calc(100% + 4px);z-index:12;width:min(375px,80vw);max-height:310px;overflow:auto;background:var(--wt-surface);color:var(--wt-text);border:1px solid var(--wt-line);border-radius:6px;box-shadow:0 6px 18px #0002}
ul{margin:0;padding:0;list-style:none}small{display:block;padding:9px 12px;color:var(--wt-muted)}
li{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:8px 12px;cursor:pointer;font-size:13px}li[aria-selected=true]{background:var(--el-fill-color-light);box-shadow:inset 2px 0 var(--wt-text)}li span{font-size:12px;color:var(--wt-muted)}b{font-weight:550}
</style>
