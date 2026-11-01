<template>
  <el-button v-if="allowed" link type="danger" :disabled="active" :loading="pending"
    :title="active?'任务结束后可删除':'删除本次记录和报告'" @click="remove">删除</el-button>
</template>
<script setup>
import {computed,ref} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {useSessionStore} from '@/store'

const props=defineProps({asset:String,mode:{type:String,default:'native'},task:String,strategy:String,title:String,status:String})
const emit=defineEmits(['deleted'])
const session=useSessionStore(),pending=ref(false)
const allowed=computed(()=>{
  const user=session.cache.userinfo
  return !user?.products?.length && (['admin','superman'].includes(user?.role) ||
    (props.asset==='futures' && user?.role==='researcher'))
})
const active=computed(()=>['queued','pending','running','stopping','preparing'].includes(props.status))
async function remove(){
  if(active.value || pending.value)return
  const {asset,mode,task,strategy,title}=props
  const [path,body]={
    native:asset==='futures'?['/bt/delstrabt',{btid:task,straid:strategy}]:['/mgr/assets/'+asset+'/delete-run',{task}],
    plus:['/bt/plus/delete',{taskid:task}],
    expiry:['/mgr/assets/option/expiry-delete',{run:task}],
    research:['/mgr/research/experiment_delete',{experiment_id:task}]
  }[mode]
  pending.value=true
  try{
    try{
      await ElMessageBox.confirm('删除「'+(title||'回测')+'」的这次记录和报告？任务编号 '+task+'。策略、行情和其他任务会保留。',
        '删除回测记录',{type:'warning',confirmButtonText:'删除记录和报告',cancelButtonText:'取消',autofocus:false})
    }catch{return}
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    const data=await response.json()
    if(!response.ok || data.result!==0)throw Error(data.message || '删除失败，请刷新后重试')
    ElMessage.success('回测记录和报告已删除')
    emit('deleted',task)
  }catch(e){ElMessage.error(e.message)}finally{pending.value=false}
}
</script>
