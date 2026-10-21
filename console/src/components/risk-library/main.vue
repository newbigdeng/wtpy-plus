<template>
 <section class="risk-library">
  <header><div><h1>风控库</h1><p>同一份规则可用于组合、原生回测与 Plus 回测。</p></div><div><el-button @click="load">刷新</el-button><el-button type="primary" @click="create">新建规则</el-button></div></header>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <el-alert title="规则在发布时固定版本。修改风控库后，需重新选择并应用组合计划；不会热改运行中的交易。" type="info" :closable="false"/>
  <el-table :data="rules" empty-text="尚无规则。新建一条规则，再到组合或回测中选择。" :loading="loading">
   <el-table-column prop="name" label="规则名称" min-width="160"/>
   <el-table-column label="类型" min-width="220"><template #default="{row}">{{types[row.type]?.label||row.type}}</template></el-table-column>
   <el-table-column label="阈值" width="130"><template #default="{row}">{{row.params.limit}} {{types[row.type]?.unit}}</template></el-table-column>
   <el-table-column label="动作" width="110"><template #default="{row}"><el-tag :type="row.mode==='pretrade'?'warning':'info'">{{row.mode==='pretrade'?'拦截增仓':'只告警'}}</el-tag></template></el-table-column>
   <el-table-column label="版本" width="80"><template #default="{row}">v{{row.version}}</template></el-table-column>
   <el-table-column label="操作" width="160"><template #default="{row}"><el-button text @click="edit(row)">{{canEdit?'编辑':'查看'}}</el-button><el-button text type="danger" @click="remove(row)">移出库</el-button></template></el-table-column>
  </el-table>
  <div class="risk-notes"><p>交易前规则检查当前目标或委托。触发时阻止新增仓位，反手保留减仓部分。</p><p>“只告警”只写风险记录，不拦截交易。原生 SimpleRiskMon 和后台资金预警仍在组合风控中心。</p><p>可用资金来自原生柜台回执。快速 Mocker 没有保证金账簿，这类检查请使用 Plus。</p></div>
  <el-dialog v-model="open" :title="form.version?'规则 · v'+form.version:'新建风控规则'" width="min(560px,94vw)">
   <el-form label-position="top" :disabled="!canEdit"><el-form-item label="ID"><el-input v-model="form.id" :disabled="Boolean(form.version)" placeholder="例如 rb_position_limit"/></el-form-item><el-form-item label="名称"><el-input v-model="form.name"/></el-form-item>
    <el-form-item label="风控类型"><el-select v-model="form.type"><el-option v-for="(item,key) in types" :key="key" :value="key" :label="item.label"/></el-select></el-form-item>
    <el-form-item :label="'阈值（'+(types[form.type]?.unit||'')+'）'"><el-input-number v-model="form.params.limit" :min="0.01" :max="types[form.type]?.max||1000000" :controls="false"/></el-form-item>
    <el-form-item label="触发动作"><el-radio-group v-model="form.mode"><el-radio-button value="pretrade">拦截新增仓位</el-radio-button><el-radio-button value="alert_only">只记录告警</el-radio-button></el-radio-group></el-form-item>
   </el-form><template #footer><el-button @click="open=false">关闭</el-button><el-button v-if="canEdit" type="primary" :loading="saving" @click="save">{{form.version?'保存新版本':'创建规则'}}</el-button></template>
  </el-dialog>
 </section>
</template>
<script>
const empty=()=>({id:'',name:'',type:'max_position_lots',mode:'pretrade',params:{limit:10},version:0})
export default {
 name:'RiskLibrary',data:()=>({rules:[],types:{},canEdit:false,loading:false,saving:false,error:'',open:false,form:empty()}),mounted(){this.load()},
 methods:{async request(action,body={}){const response=await fetch('/mgr/risk-library/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(data.result<0)throw new Error(data.message);return data},async load(){this.loading=true;this.error='';try{const data=await this.request('list');this.rules=data.rules;this.types=data.types;this.canEdit=data.can_edit}catch(error){this.error=error.message}finally{this.loading=false}},deny(){this.$message.warning('游客模式不支持操作')},create(){if(!this.canEdit)return this.deny();this.form=empty();this.open=true},edit(row){this.form=JSON.parse(JSON.stringify(row));this.open=true},async save(){this.saving=true;try{const data=await this.request('save',{rule:this.form});this.$message.success(data.message);this.open=false;await this.load()}catch(error){this.$message.error(error.message)}finally{this.saving=false}},async remove(row){if(!this.canEdit)return this.deny();try{await this.$confirm('移出后新任务无法选择此规则。已发布任务仍使用固定版本。','移出风控库',{type:'warning'});await this.request('delete',{id:row.id,version:row.version});await this.load()}catch(error){if(error!=='cancel'&&error!=='close')this.$message.error(error.message)}}}
}
</script>
<style scoped>
.risk-library{padding:28px;min-height:100%;overflow:auto;color:var(--el-text-color-primary)}header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:20px}h1{font-size:23px;margin:0 0 8px}header p,.risk-notes{color:var(--el-text-color-secondary);line-height:1.7;font-size:13px}header p{margin:0}.el-alert{margin-bottom:16px}.risk-notes{margin:20px 0}.el-select{width:100%}
</style>
