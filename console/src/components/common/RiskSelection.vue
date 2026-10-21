<template>
 <section class="risk-selection">
  <div class="risk-choice-title"><strong>交易前风控</strong><el-button text size="small" :loading="loading" @click="load">刷新风控库</el-button></div>
  <el-select :model-value="selected" multiple clearable filterable :disabled="disabled" placeholder="不附加规则（保留原生柜台保护）" @update:model-value="change">
   <el-option v-for="rule in options" :key="ruleKey(rule)" :value="ruleKey(rule)" :label="`${rule.name} · v${rule.version} · ${rule.mode==='alert_only'?'只告警':'拦截增仓'}`"/>
  </el-select>
  <p>在风控库新建规则后选择。多个规则同时生效；任一拦截即阻止增仓，减仓与撤单仍可执行。运行任务固定版本。</p>
  <p v-if="engineType==='HFT'">HFT 检查订单与在途委托；快速 HFT 回测的资金风控请改用 Plus。</p>
  <p v-if="error" class="risk-choice-error">{{error}}</p>
 </section>
</template>
<script>
export default {
 name:'RiskSelection',props:{modelValue:{type:Object,default:()=>({schema:1,rules:[]})},engineType:{type:String,default:'CTA'},disabled:Boolean},emits:['update:modelValue'],
 data:()=>({rules:[],loading:false,error:''}),
 computed:{selected(){return (this.modelValue?.rules||[]).map(this.ruleKey)},options(){const all=[...this.rules];for(const row of this.modelValue?.rules||[])if(!all.some(item=>this.ruleKey(item)===this.ruleKey(row)))all.push({...row,name:row.name||row.id});return all}},
 mounted(){this.load()},
 methods:{ruleKey(rule){return rule.id+'@'+rule.version},async load(){this.loading=true;this.error='';try{const response=await fetch('/mgr/risk-library/list',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const data=await response.json();if(data.result<0)throw new Error(data.message);this.rules=data.rules}catch(error){this.error=error.message}finally{this.loading=false}},change(keys){const rules=keys.map(key=>this.options.find(rule=>this.ruleKey(rule)===key)).map(rule=>({id:rule.id,version:rule.version}));this.$emit('update:modelValue',{schema:1,rules})}}
}
</script>
<style scoped>
.risk-selection{padding:16px;border:1px solid var(--el-border-color);border-radius:10px;margin:12px 0}.risk-choice-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.risk-selection .el-select{width:100%}.risk-selection p{font-size:12px;line-height:1.6;color:var(--el-text-color-secondary);margin:8px 0 0}.risk-selection .risk-choice-error{color:var(--el-color-danger)}
</style>
