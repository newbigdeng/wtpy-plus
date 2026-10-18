<template>
 <span class="process-control">
  <span v-if="showState" class="process-state" :class="{running}" title="组合进程状态，不代表柜台已登录"><i/>{{running?'运行中':'已停止'}}</span>
  <el-button :type="running?'danger':'success'" plain :loading="busy" :disabled="disabled" :aria-label="(running?'停止进程 ':'启动进程 ')+id" @click="control">{{running?'停止进程':'启动进程'}}</el-button>
 </span>
</template>
<script>
export default {
 props:{id:{type:String,required:true},name:{type:String,default:''},running:Boolean,kind:{type:String,default:'app'},showState:{type:Boolean,default:true},disabled:Boolean},
 emits:['changed'],data:()=>({busy:false}),
 methods:{async control(){
  if(this.busy || !this.id)return;
  const stopping=this.running, name=this.name||this.id;
  try{
   await this.$confirm(stopping?'停止「'+name+'」？停止进程不会自动平仓。后续定时任务仍可能再次启动。':'启动「'+name+'」？程序将按当前配置运行，已获交易许可的策略可能下单。',stopping?'停止进程':'启动进程',{type:'warning',confirmButtonText:stopping?'停止进程':'启动进程',cancelButtonText:'取消'});
   this.busy=true;
   const method=(stopping?'stop':'start')+(this.kind==='group'?'Group':'App');
   const result=await new Promise(resolve=>this.$api[method](this.id,resolve));
   if(result.result<0)throw new Error(result.message);
   this.$message.success('命令已提交，正在读取进程状态');
   this.$emit('changed');
  }catch(error){if(error instanceof Error)this.$message.error(error.message)}finally{this.busy=false}
 }}
}
</script>
<style scoped>.process-control{display:inline-flex;gap:10px;align-items:center;white-space:nowrap}.process-state{display:inline-flex;gap:6px;align-items:center;color:var(--wt-muted);font-size:11px}.process-state i{width:6px;height:6px;border-radius:50%;background:currentColor}.process-state.running{color:var(--wt-success)}</style>
