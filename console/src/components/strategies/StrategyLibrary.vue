<template>
 <section class="strategy-library" v-loading="loading">
  <header><div><h1>策略管理</h1><p>在这里维护 Python 策略。组合和研究使用固定版本的副本。</p></div><div class="library-actions"><el-button @click="load">刷新</el-button><el-button @click="chooseResearch">从已有研究复制</el-button><el-button @click="create">新建策略</el-button><el-button type="primary" @click="chooseFile">导入 .py</el-button><input ref="file" type="file" accept=".py,text/x-python" hidden @change="importFile"/></div></header>
  <div class="library-filter"><el-radio-group v-model="kind"><el-radio-button value="">全部</el-radio-button><el-radio-button v-for="item in ['CTA','HFT','SEL']" :key="item" :value="item">{{item}}</el-radio-button></el-radio-group><el-input v-model="search" placeholder="搜索名称、ID 或策略类" clearable aria-label="搜索策略库"/><span>{{filtered.length}} 个策略</span></div>
  <el-alert v-if="!canManage" type="info" :closable="false" title="游客可以查看策略目录，不能读取源码或修改策略。"/>
  <el-table :data="filtered" stripe empty-text="策略库为空。管理员可导入 Python 文件或创建策略框架。" @row-dblclick="open">
   <el-table-column label="策略" min-width="200"><template #default="{row}"><strong>{{row.name}}</strong><small>{{row.id}} · {{row.class_name}}</small></template></el-table-column>
   <el-table-column label="类型" width="90"><template #default="{row}"><el-tag effect="plain">{{row.engine_type}}</el-tag></template></el-table-column>
   <el-table-column label="版本" width="100"><template #default="{row}">v{{row.version}}</template></el-table-column>
   <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip/>
   <el-table-column label="操作" width="170"><template #default="{row}"><el-button link @click="open(row)">{{canManage?'代码与版本':'查看'}}</el-button><el-button link type="danger" @click="remove(row)">删除</el-button></template></el-table-column>
  </el-table>
  <p class="library-note">删除只从目录移除条目，组合快照和历史版本保留。库中保存新版本不会改变已应用的组合计划。</p>
  <el-dialog v-model="showCreate" title="新建策略框架" width="420px"><el-form label-position="top"><el-form-item label="策略类型"><el-select v-model="createKind"><el-option v-for="item in ['CTA','HFT','SEL']" :key="item" :value="item" :label="item"/></el-select></el-form-item></el-form><template #footer><el-button @click="showCreate=false">取消</el-button><el-button type="primary" @click="createTemplate">生成框架</el-button></template></el-dialog>
  <el-dialog v-model="showResearch" title="复制已有研究到策略库" width="520px"><el-form label-position="top"><el-form-item label="研究策略"><el-select v-model="researchId" placeholder="选择当前账户的研究" aria-label="复制已有研究"><el-option v-for="row in researches" :key="row.id" :value="row.id" :label="row.name+' · '+row.engine_type"/></el-select></el-form-item><el-form-item label="新的策略库 ID"><el-input v-model="researchLibraryId" placeholder="例如 rb_trend" aria-label="研究副本策略库ID"/></el-form-item></el-form><el-alert type="info" :closable="false" title="只复制当前源码。原研究、历史回测和组合都不会改变。"/><template #footer><el-button @click="showResearch=false">取消</el-button><el-button type="primary" :loading="saving" @click="importResearch">复制到库</el-button></template></el-dialog>
  <el-dialog v-model="showEditor" :title="draft.id?'策略：'+draft.name:'导入策略'" width="min(1160px,96vw)" :close-on-click-modal="false">
   <el-form label-position="top" :disabled="!canManage"><div class="library-meta"><el-form-item label="策略 ID"><el-input v-model="draft.id" :disabled="Boolean(draft.version)" placeholder="字母开头，最多40字符" aria-label="策略库ID"/></el-form-item><el-form-item label="显示名称"><el-input v-model="draft.name" maxlength="80" aria-label="策略库名称"/></el-form-item><el-form-item label="版本"><el-input :model-value="draft.version?'v'+draft.version+' · 保存后生成新版本':'尚未保存'" disabled/></el-form-item></div><el-form-item label="说明"><el-input v-model="draft.description" maxlength="2000" aria-label="策略库说明"/></el-form-item></el-form>
   <div v-if="canManage" class="library-editor"><CodeEditor v-model="draft.source" :options="editorOptions"/></div><el-empty v-else description="策略源码仅管理员可见"/>
   <p class="library-note">导入和保存只检查语法与参数。策略代码将在回测或手动启动组合时执行，请只导入可信源码。</p>
   <template #footer><el-button @click="showEditor=false">关闭</el-button><el-button v-if="canManage" type="primary" :loading="saving" @click="save">{{draft.version?'保存新版本':'加入策略库'}}</el-button></template>
  </el-dialog>
 </section>
</template>

<script>
import CodeEditor from '@/components/common/CodeEditor.vue'
import {useSessionStore} from '@/store.js'
const blank=()=>({id:'',name:'',description:'',source:'',version:null})
export default {
 name:'StrategyLibrary',components:{CodeEditor},
 data:()=>({strategies:[],loading:false,saving:false,kind:'',search:'',showEditor:false,showCreate:false,createKind:'CTA',draft:blank(),showResearch:false,researches:[],researchId:'',researchLibraryId:''}),
 computed:{
  canManage(){return ['admin','superman'].includes(useSessionStore().cache.userinfo?.role)},
  filtered(){const query=this.search.trim().toLowerCase();return this.strategies.filter(row=>(!this.kind||row.engine_type===this.kind)&&(!query||[row.id,row.name,row.class_name,row.description].join(' ').toLowerCase().includes(query)))},
  editorOptions(){return {mode:'python',indentUnit:4,readOnly:!this.canManage,extraKeys:{'Ctrl-S':()=>this.save()}}}
 },
 methods:{
  async request(path,body={}){const response=await fetch('/mgr/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok||result.result<0)throw new Error(result.message||'策略库请求失败');return result},
  permit(){if(!this.canManage){this.$message.warning('游客模式不支持操作');return false}return true},
  async load(){this.loading=true;try{this.strategies=(await this.request('strategy_library')).strategies||[]}catch(error){this.$message.error(error.message)}finally{this.loading=false}},
  chooseFile(){if(this.permit())this.$refs.file.click()},
  async importFile(event){const file=event.target.files?.[0];event.target.value='';if(!file||!this.permit())return;if(file.size>262144){this.$message.error('Python文件不可超过256KB');return}this.draft={...blank(),id:file.name.replace(/\.py$/i,'').replace(/[^A-Za-z0-9_-]/g,'_').slice(0,40),name:file.name.replace(/\.py$/i,''),source:await file.text()};this.showEditor=true},
  create(){if(this.permit()){this.createKind=this.kind||'CTA';this.showCreate=true}},
  async chooseResearch(){if(!this.permit())return;try{this.researches=(await this.request('strategy_library_research')).strategies||[];this.researchId=this.researches[0]?.id||'';this.researchLibraryId='';this.showResearch=true}catch(error){this.$message.error(error.message)}},
  async importResearch(){if(!this.permit()||this.saving)return;this.saving=true;try{const value=await this.request('strategy_library_import_research',{research_id:this.researchId,library_id:this.researchLibraryId});this.draft=value.strategy;this.showResearch=false;this.showEditor=true;this.$message.success(value.message);await this.load()}catch(error){this.$message.error(error.message)}finally{this.saving=false}},
  async createTemplate(){try{this.draft={...blank(),source:(await this.request('strategy_library_template',{engine_type:this.createKind})).source};this.showCreate=false;this.showEditor=true}catch(error){this.$message.error(error.message)}},
  async open(row){try{this.draft=(await this.request('strategy_library_get',{library_id:row.id,version:row.version})).strategy;this.showEditor=true}catch(error){this.$message.error(error.message)}},
  async save(){if(!this.permit()||this.saving)return;this.saving=true;try{if(!/^[A-Za-z][A-Za-z0-9_-]{0,39}$/.test(this.draft.id))throw new Error('ID须以英文字母开头，只能包含字母、数字、下划线或短横线，最多40字符');const result=await this.request('strategy_library_save',{library_id:this.draft.id,name:this.draft.name,description:this.draft.description||'',source:this.draft.source,base_version:this.draft.version||null});this.draft=result.strategy;this.$message.success(result.message);await this.load()}catch(error){this.$message.error(error.message)}finally{this.saving=false}},
  async remove(row){if(!this.permit())return;try{await this.$confirm('从策略库移除“'+row.name+'”？组合内已选版本和历史记录仍保留。','删除库策略',{type:'warning',confirmButtonText:'从目录移除',cancelButtonText:'取消'});const value=await this.request('strategy_library_delete',{library_id:row.id,base_version:row.version});this.$message.success(value.message);await this.load()}catch(error){if(error instanceof Error)this.$message.error(error.message)}}
 },mounted(){this.load()}
}
</script>

<style scoped>
.strategy-library{height:100%;overflow:auto;padding:24px;background:var(--wt-surface);color:var(--wt-text);box-sizing:border-box}.strategy-library header{display:flex;gap:20px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:22px}.strategy-library h1{font-size:21px;margin:0 0 8px}.strategy-library p,.library-note{font-size:12px;color:var(--wt-muted);line-height:1.8;margin:0}.library-actions,.library-filter{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.library-filter{margin-bottom:18px}.library-filter .el-input{max-width:320px;margin-left:auto}.library-filter>span{font-size:12px;color:var(--wt-muted)}.strategy-library small{display:block;color:var(--wt-muted);font:11px/1.8 var(--wt-mono);margin-top:5px}.library-note{margin-top:16px}.library-meta{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}.library-editor{height:52vh;border:1px solid var(--wt-line);border-radius:7px;overflow:hidden}@media(max-width:700px){.strategy-library{padding:14px}.library-meta{grid-template-columns:1fr}.library-filter .el-input{margin-left:0}}
</style>
