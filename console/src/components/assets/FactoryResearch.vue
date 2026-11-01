<template><section class="factory-panel">
  <h3>原生 C++ 期权策略</h3>
  <p v-if="!items.length">{{message || (loading?'正在读取已安装的策略…':'当前账户还没有安装 C++ 期权策略。')}}</p>
  <template v-else>
    <label>策略版本<select aria-label="C++ 期权策略" v-model="selected"><option v-for="f in items" :key="f.id" :value="f">{{f.title}} · v{{f.version}}</option></select></label>
    <p>{{selected.codes.join('、')}}</p><p>{{selected.fees}}。信号取收盘后的资料，按后续日线回放成交。</p>
    <form @submit.prevent="launch">
      <label>开始日期<input type="date" v-model="start" :min="selected.start" :max="end" required></label>
      <label>结束日期<input type="date" v-model="end" :min="start" :max="selected.end" required></label>
      <label>初始资金<input type="number" min="1" v-model.number="capital" required></label>
      <label>组合份数<input type="number" min="1" max="5" v-model.number="units" required></label>
      <button :disabled="busy || !canRun || start>=end">{{busy?'正在生成独立任务…':'启动历史回测'}}</button>
    </form>
    <p role="status">{{message}}</p>
    <router-link v-if="task" :to="{name:'run-report',query:{asset:'option',strategy:task.straid,task:task.id}}">打开本次报告</router-link>
  </template>
</section></template>
<script setup>
import {ref,watch,onMounted} from 'vue'
import {useRoute} from 'vue-router'
const props=defineProps({api:Function})
const emit=defineEmits(['launched'])
const items=ref([]),selected=ref(null),start=ref(''),end=ref(''),capital=ref(1000000),units=ref(1)
const route=useRoute(),loading=ref(true),canRun=ref(false),busy=ref(false),message=ref(''),task=ref(null)
watch(selected,f=>{if(f){start.value=f.start;end.value=f.end}task.value=null;message.value=''})
onMounted(async()=>{
  try{const r=await props.api('factories');items.value=r.factories;canRun.value=r.can_run;selected.value=items.value.find(f=>f.id===route.query.factory)||items.value[0]||null}
  catch(e){message.value=e.message}finally{loading.value=false}
})
async function launch(){
  if(busy.value)return
  busy.value=true;task.value=null;message.value=''
  try{const r=await props.api('factory-run',{factory_id:selected.value.id,version:selected.value.version,start:start.value,end:end.value,capital:capital.value,units:units.value})
    task.value=r.task;message.value='已创建独立回测任务。';emit('launched')
  }catch(e){message.value=e.message}finally{busy.value=false}
}
</script>
<style scoped>form{display:flex;gap:12px;flex-wrap:wrap;align-items:end}label{display:grid;gap:5px}input,select,button{padding:7px;color:inherit;background:var(--wt-surface);border:1px solid var(--wt-line)}p{color:var(--wt-muted)}</style>
