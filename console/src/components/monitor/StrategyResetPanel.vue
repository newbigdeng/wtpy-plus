<template>
 <el-dialog v-model="visible" :title="'重置策略：'+(target.name||target.id||'')" width="min(620px, 94vw)" append-to-body :close-on-click-modal="false">
  <p class="reset-explanation">先柜台平仓，确认后清空当前信号与理论持仓。旧记录归档，实际成交与盈亏保留。</p>
  <p class="reset-explanation">后台先暂停策略、处理委托并退仓。柜台确认空仓且无活动委托后才清理；成功或失败后均保持暂停，不自动恢复交易。</p>
  <el-alert v-if="!loading&&!target.reset_supported" :title="target.reset_reason||'当前策略暂不支持重置'" type="warning" :closable="false" show-icon/>
  <el-alert v-if="task?.status==='failed'&&task.native_completed" title="原生清理已执行，策略重建未完成。保持暂停，请先处理错误。" type="warning" :closable="false" show-icon/>
  <section v-if="task" class="reset-task" role="status" aria-label="策略重置进度">
   <strong>{{statusLabel(task.status)}} · {{stepLabel(task.step)}}</strong>
   <span>{{task.message||'等待后台回执'}}</span>
   <small v-if="task.updated_at">更新时间 {{formatTime(task.updated_at)}}</small>
   <small v-if="task.archive||task.archive_path">旧记录已归档；实际成交与盈亏仍保留。</small>
  </section>
  <el-alert v-if="task?.error||error" :title="task?.error||error" type="error" :closable="false" show-icon/>
  <template #footer><el-button @click="visible=false">{{active?'收起进度':'关闭'}}</el-button><el-button v-if="task" :loading="loading" @click="refresh">刷新进度</el-button><el-button type="danger" :disabled="!target.reset_supported||active||loading" :loading="starting" @click="start">确认重置</el-button></template>
 </el-dialog>
</template>
<script>
export default{
 name:'StrategyResetPanel',props:{groupid:{type:String,required:true}},emits:['task','changed'],
 data:()=>({visible:false,target:{},task:null,error:'',loading:false,starting:false,serial:0}),
 computed:{active(){return ['queued','running'].includes(this.task?.status)}},
 methods:{
  statusLabel(status){return {queued:'已排队',running:'重置中',completed:'重置完成',failed:'重置失败'}[status]||'等待回执'},
  stepLabel(step){return {pausing:'暂停策略',cancelling:'处理活动委托',flattening:'柜台退仓',confirming:'确认空仓与委托',archiving:'归档并清理当前状态',reinitializing:'重建策略状态',completed:'当前状态已清理，策略保持暂停',failed:'保持暂停，等待处理'}[step]||'准备重置'},
  formatTime(value){return new Date(value*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})},
  async request(path,extra={}){const response=await fetch('/mgr/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid:this.groupid,strategyid:this.target.id,...extra})});const result=await response.json();if(!response.ok||result.result<0)throw new Error(result.message||'策略重置请求失败');return result},
  async open(row){
   clearTimeout(this.timer);const serial=++this.serial;this.target={...row};this.task=row.reset_task||null;this.error='';this.loading=true;this.visible=true;
   try{if(row.reset_supported===undefined){const result=await this.request('strategy_plan');if(serial!==this.serial)return;this.target={...this.target,...result.strategy_status?.[row.id]};if(result.can_manage===false)this.target.reset_supported=false}if(serial===this.serial)await this.refresh()}
   catch(error){if(serial===this.serial){this.error=error.message;this.loading=false}}
  },
  accept(task){const previous=this.task;this.task=task;if(task)this.$emit('task',task);if(task&&(task.status==='completed'&&previous?.status!=='completed'||task.native_completed&&!previous?.native_completed))this.$emit('changed',task);clearTimeout(this.timer);if(this.active)this.timer=setTimeout(()=>this.refresh(),1500)},
  async refresh(){const serial=++this.serial;this.loading=true;this.error='';try{const result=await this.request('reset_strategy_status');if(serial===this.serial)this.accept(result.task||null)}catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.loading=false}},
  async start(){if(!this.target.reset_supported||this.active||this.starting)return;const serial=++this.serial;this.starting=true;this.error='';try{const result=await this.request('reset_strategy',{confirmed:true});if(serial===this.serial)this.accept(result.task)}catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.starting=false}}
 },
 watch:{groupid(){clearTimeout(this.timer);++this.serial;this.visible=false;this.task=null;this.target={};this.loading=false;this.starting=false}},
 beforeUnmount(){++this.serial;clearTimeout(this.timer)}
}
</script>
<style scoped>
.reset-explanation{font-size:12px;line-height:1.8;color:var(--wt-muted);margin:0 0 12px}.reset-task{display:flex;flex-direction:column;gap:8px;padding:14px;margin:12px 0;border:1px solid var(--wt-line);border-radius:7px;color:var(--wt-text);font-size:12px;line-height:1.7}.reset-task small{color:var(--wt-muted);font-size:11px}
</style>
