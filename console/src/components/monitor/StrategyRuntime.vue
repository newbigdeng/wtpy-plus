<template>
 <section class="runtime-panel">
  <div class="runtime-heading">
   <div><h2>组合内策略</h2><p>配置下次启动计划，或管理当前策略的决策和目标过滤。</p></div>
   <div class="runtime-heading-actions"><StrategySetupGuide/><el-button v-if="tab==='runtime'" :loading="loading" @click="load">刷新状态</el-button></div>
  </div>
  <el-tabs v-model="tab"><el-tab-pane label="策略与启动计划" name="plan"/><el-tab-pane label="运行控制" name="runtime"/></el-tabs>
  <ManagedStrategies v-if="tab==='plan'" :groupid="groupid" :running="running" @changed="$emit('changed',$event)"/>
  <template v-else>
  <el-alert type="info" :closable="false" show-icon
   title="暂停决策保留原目标。过滤把本策略的目标贡献归零，经后续 CTA 调度交给执行器退仓；配置已写入不代表已经成交。"/>
  <el-table :data="strategies" stripe class="runtime-table" empty-text="暂无已登记策略；请确认组合已启动并注册策略">
   <el-table-column prop="name" label="策略" min-width="145">
    <template #default="{row}"><strong>{{row.name || row.id}}</strong><el-button v-if="canControl" link size="small" @click="rename(row)" :aria-label="'重命名策略 '+(row.name||row.id)">改名</el-button><small class="runtime-id">{{row.id}}</small></template>
   </el-table-column>
   <el-table-column label="引擎" width="70"><template #default="{row}">{{row.engine?.toUpperCase() || '—'}}</template></el-table-column>
   <el-table-column label="运行合约" min-width="180"><template #default="{row}"><el-tag v-for="code in row.applied_contracts||[]" :key="code" effect="plain">{{code}}</el-tag><small class="runtime-reason">{{row.applied_contracts?.length?(row.state==='stopped'?'启动配置 · 进程已停止':'已应用的启动配置'):'未识别到合约配置'}}</small><small v-if="row.plan_pending" class="runtime-reason">草稿：{{row.planned_contracts?.join('、')||'无'}}</small></template></el-table-column>
   <el-table-column label="计划与持仓" min-width="180"><template #default="{row}"><el-tag :type="row.new_strategy?'info':'success'" effect="plain">{{row.new_strategy?'新增实例':'已有策略'}}</el-tag><small class="runtime-reason">{{row.has_positions?'有持仓 · '+row.position_volume+'手':row.position_known?'账簿无持仓':'尚无持仓快照'}}</small><small class="runtime-reason">{{row.in_launch_plan?(row.plan_pending?'计划等待应用或下次启动':'已在启动计划'):'原入口／历史账簿，未加入页面计划'}}</small></template></el-table-column>
   <el-table-column label="决策状态" min-width="190">
    <template #default="{row}">
     <span :class="row.state==='running'?'runtime-running':''">{{row.pending?'等待进程回执':stateName(row.state)}}</span>
     <small class="runtime-reason">{{row.control_reason}}</small>
    </template>
   </el-table-column>
   <el-table-column label="目标过滤" min-width="220">
    <template #default="{row}">
     <el-tag :type="row.filter_enabled?'warning':'info'" effect="plain">{{filterName(row)}}</el-tag>
     <small class="runtime-reason">{{row.filter_reason || '当前组合尚未提供过滤状态'}}</small>
    </template>
   </el-table-column>
   <el-table-column label="操作" min-width="260">
    <template #default="{row}">
     <div class="runtime-actions">
      <el-button size="small" :type="row.state==='paused'?'success':'warning'" plain
       :disabled="!isGuest&&(!canControl || Boolean(busy) || !row.control_supported || row.pending || !['running','paused'].includes(row.state))"
       :loading="busy===row.id+':decision'" @click="control(row)">
       {{row.state==='paused'?'恢复决策':'暂停决策'}}
      </el-button>
      <el-button size="small" :type="row.filter_enabled?'success':'danger'" plain
       :disabled="!isGuest&&(!canControl || Boolean(busy) || !row.filter_supported)"
       :loading="busy===row.id+':filter'" @click="filter(row)">
       {{row.filter_enabled?'解除过滤':'过滤并退仓'}}
      </el-button>
     </div>
    </template>
   </el-table-column>
  </el-table>
  <p class="runtime-note">两项控制可以同时启用。过滤期间策略仍可计算并更新理论仓位；解除过滤可能重新建立这些仓位，即使决策仍暂停。停止进程不会清空历史信号或自动平仓。退仓进度请核对通道持仓、委托和成交。仅管理员可操作。</p>
  </template>
 </section>
</template>

<script>
import StrategySetupGuide from './StrategySetupGuide.vue'
import ManagedStrategies from './ManagedStrategies.vue'
import {useSessionStore} from '@/store.js'
export default {
 name:'StrategyRuntime',
 components:{StrategySetupGuide,ManagedStrategies},
 props:{groupid:{type:String,required:true},running:{type:Boolean,default:undefined}},
 emits:['changed'],
 data:()=>({strategies:[],loading:false,busy:'',serial:0,canControl:true,tab:'plan'}),
 computed:{isGuest(){return useSessionStore().cache.userinfo?.role==='guest'}},
 methods:{
  stateName(state){return {running:'运行中',paused:'决策已暂停',stopped:'进程已停止',unknown:'等待状态'}[state]||state},
  filterName(row){return row.filter_enabled?(row.filter_zero_target?'已配置归零 · 待核对退仓':'已配置其他过滤'):(row.filter_supported?'未过滤':'不支持过滤')},
  async request(path,data){
   const response=await fetch('/mgr/'+path,{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(data)})
   const result=await response.json()
   if(result.result<0)throw new Error(result.message)
   return result
  },
  async load(){
   const serial=++this.serial;this.loading=true
   try{
    const result=await this.request('qrystra_runtime',{groupid:this.groupid})
    if(serial===this.serial){this.strategies=result.strategies||[];this.canControl=result.can_control!==false}
   }catch(error){if(serial===this.serial)this.$message.error(error.message)}
   finally{if(serial===this.serial)this.loading=false}
  },
  async rename(row){
   const groupid=this.groupid
   try{
    const {value}=await this.$prompt('修改显示名称，策略ID和账簿目录保持不变。','重命名策略',{inputValue:row.name||row.id,confirmButtonText:'保存',cancelButtonText:'取消',inputValidator:value=>Boolean(value?.trim())&&value.trim().length<=80||'请输入1至80个字符'})
    if(groupid!==this.groupid)return
    await this.request('renamestra',{groupid,strategyid:row.id,name:value.trim()})
    await this.load();this.$message.success('策略显示名称已修改')
   }catch(error){if(error instanceof Error)this.$message.error(error.message)}
  },
  async control(row){
   if(this.isGuest){this.$message.warning('游客模式不支持操作');return}
   const groupid=this.groupid,action=row.state==='paused'?'resume':'pause'
   const text=action==='pause'
    ?'暂停后跳过后续决策回调，并阻止新交易提交。原有持仓、委托、条件单及执行器目标仍可能继续。目标过滤状态保持不变。确认暂停决策？'
    :'恢复后接收后续行情并可能提交新交易指令；不补跑暂停期间的信号。'+(row.filter_enabled?'本策略仍处于过滤状态，目标继续受过滤配置约束。':'')+'确认恢复决策？'
   try{
    await this.$confirm(text,action==='pause'?'暂停策略决策':'恢复策略决策',{type:'warning',confirmButtonText:'确认',cancelButtonText:'取消'})
    if(groupid!==this.groupid)return
    this.busy=row.id+':decision'
    const result=await this.request('ctrlstra',{groupid,strategyid:row.id,action})
    await this.load();this.$message.info(result.pending?'命令已提交，等待当前回调结束及进程回执':'决策状态已确认')
   }catch(error){if(error instanceof Error)this.$message.error(error.message)}finally{this.busy=''}
  },
  async filter(row){
   if(this.isGuest){this.$message.warning('游客模式不支持操作');return}
   const groupid=this.groupid,action=row.filter_enabled?'unfilter':'filter'
   const text=action==='filter'
    ?'将本策略的目标贡献设为 0。后续 CTA 调度会按组合净目标交由执行器退仓，其他策略的目标继续保留；已有挂单和成交结果仍需核对。策略决策状态保持不变。确认过滤并退仓？'
    :'解除后，策略的当前理论仓位会重新参与组合目标，可能立即重新建仓，即使该策略仍暂停决策。确认解除过滤？'
   try{
    await this.$confirm(text,action==='filter'?'过滤并退仓':'解除策略过滤',{type:'warning',confirmButtonText:'确认',cancelButtonText:'取消'})
    if(groupid!==this.groupid)return
    this.busy=row.id+':filter'
    await this.request('ctrlstra_filter',{groupid,strategyid:row.id,action})
    await this.load();this.$message.info(action==='filter'?'过滤配置已写入，等待 CTA 调度；请核对通道退仓结果':'过滤已解除；请关注原理论仓位恢复及通道委托')
   }catch(error){if(error instanceof Error)this.$message.error(error.message)}finally{this.busy=''}
  }
 },
 watch:{groupid(){this.load()}},
 mounted(){this.load();this.poll=setInterval(()=>this.load(),3000)},
 beforeUnmount(){++this.serial;clearInterval(this.poll)}
}
</script>

<style scoped>
.runtime-heading-actions{display:flex;gap:8px;flex-wrap:wrap}
.runtime-panel{height:100%;overflow:auto;padding:20px;background:var(--wt-surface);color:var(--wt-text);min-height:100%;box-sizing:border-box}
.runtime-heading{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:18px}
.runtime-heading h2{font-size:19px;margin:0 0 7px;font-weight:650}
.runtime-heading p,.runtime-note{color:var(--wt-muted);font-size:12px;line-height:1.8;margin:0}
.runtime-table{margin-top:18px;width:100%}
.runtime-id,.runtime-reason{display:block;color:var(--wt-muted);font-size:11px;margin-top:5px;white-space:normal;line-height:1.6}
.runtime-running{color:var(--wt-success)}
.runtime-note{margin-top:18px}
.runtime-actions{display:flex;gap:6px;flex-wrap:wrap}
.runtime-actions .el-button{margin-left:0}
@media(max-width:700px){.runtime-panel{height:100%;overflow:auto;padding:12px}.runtime-heading{align-items:flex-start}.runtime-heading p{max-width:240px}}
</style>
