<template>
 <section class="group-risk-selection" aria-label="组合交易前风控" v-loading="loading">
  <header><div><h3>组合交易前风控</h3><p>从风控库选择规则，随组合启动计划加载。</p></div><el-button :loading="loading" @click="load">刷新配置</el-button></header>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <template v-if="state">
   <div class="risk-plan-status"><el-tag :type="state.managed?'success':'warning'">{{state.managed?'页面管理入口':'原启动入口尚未接入'}}</el-tag><el-tag effect="plain">{{state.running?'进程运行中 · 配置只读':'进程已停止'}}</el-tag><span>{{state.pending_changes?'有草稿等待应用':state.managed?'草稿与启动配置一致':'尚未应用页面计划'}}</span></div>
   <div class="applied-rules"><strong>已应用到启动配置</strong><span v-if="!state.managed">原 run.py 不会自动加载风控库规则。</span><template v-else-if="state.applied_risk?.rules?.length"><el-tag v-for="rule in state.applied_risk.rules" :key="rule.id+'@'+rule.version" effect="plain">{{rule.name||rule.id}} · v{{rule.version}}</el-tag></template><span v-else>未附加风控库规则；原生柜台保护仍生效。</span></div>
   <RiskSelection v-model="selection" :engine-type="state.plan.engine_type" :disabled="!editable||saving" @update:model-value="dirty=true"/>
   <p v-if="!state.can_manage">仅管理员可修改。可查看已保存的风控选择。</p>
   <p v-else-if="state.running">修改前请先停止组合。停止进程不会自动平仓。</p>
   <p v-else>保存只更新草稿。请到策略与启动计划应用配置，再手动启动。</p>
   <footer><el-button type="primary" :disabled="!editable||!dirty" :loading="saving" @click="save">保存风控草稿</el-button><router-link :to="{query:{...$route.query,tab:'runtime'}}">打开策略与启动计划</router-link><router-link to="/risk-library">打开风控库</router-link></footer>
  </template>
 </section>
</template>
<script>
import RiskSelection from '@/components/common/RiskSelection.vue'
const copy=value=>JSON.parse(JSON.stringify(value))
export default{
 name:'GroupRiskSelection',components:{RiskSelection},props:{groupid:{type:String,required:true}},
 data:()=>({state:null,selection:{schema:1,rules:[]},loading:false,saving:false,dirty:false,error:'',serial:0}),
 computed:{editable(){return this.state?.can_manage&&!this.state.running&&['CTA','HFT'].includes(this.state.plan.engine_type)}},
 methods:{
  async request(path,body){const response=await fetch('/mgr/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok||data.result<0)throw new Error(data.message||'无法读取组合风控');return data},
  accept(state){this.state=state;this.selection=copy(state.plan.risk||{schema:1,rules:[]});this.dirty=false;this.error=''},
  async load(){const serial=++this.serial;this.loading=true;try{const state=await this.request('strategy_plan',{groupid:this.groupid});if(serial===this.serial)this.accept(state)}catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.loading=false}},
  async save(){if(!this.editable||this.saving)return;const serial=this.serial,groupid=this.groupid;this.saving=true;try{const plan={...copy(this.state.plan),risk:copy(this.selection)};const state=await this.request('save_strategy_plan',{groupid,plan,apply:false});if(serial!==this.serial)return;this.accept(state);this.$message.success('风控草稿已保存；请应用启动计划后手动启动')}catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.saving=false}}
 },
 watch:{groupid(){this.state=null;this.saving=false;this.dirty=false;this.load()}},mounted(){this.load()}
}
</script>
<style scoped>
.group-risk-selection{border:1px solid var(--wt-line);border-radius:8px;padding:18px;margin-bottom:18px;background:var(--wt-surface)}header,footer,.risk-plan-status,.applied-rules{display:flex;align-items:center;gap:10px;flex-wrap:wrap}header{justify-content:space-between}h3{font-size:15px;margin:0 0 6px}p,.risk-plan-status,.applied-rules,footer{font-size:12px;line-height:1.8;color:var(--wt-muted)}p{margin:0 0 10px}.risk-plan-status{margin:10px 0}.applied-rules{padding:10px;background:var(--wt-hover);border-radius:6px}.applied-rules strong{color:var(--wt-text);font-weight:500}footer{margin-top:10px}footer a{color:var(--el-color-primary)}
</style>
