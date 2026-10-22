<template>
 <section class="history-manager">
  <header><div><h1>历史数据管理</h1><p>查看采集与导入的 WT 数据。各类型分别保存；导入日线只更新日线，不会补齐分钟线或 Tick。</p></div><el-button @click="openImport()">从外部导入</el-button><el-button :loading="loading" @click="refresh()">刷新目录</el-button></header>
  <el-alert title="DSB 保存历史行情，DMB 是采集器实时文件。删除先预览，仅删除历史 DSB；采集、组合或回测运行时拒绝删除。" type="info" :closable="false"/>
  <div class="history-filters"><el-input v-model="search" placeholder="搜索品种或合约" clearable aria-label="历史合约搜索"/><el-select v-model="period" aria-label="历史数据类型"><el-option value="" label="全部类型"/><el-option v-for="(label,key) in periods" :key="key" :value="key" :label="label"/></el-select><span>{{filtered.length}} 个数据对象</span></div>
  <el-table :data="filtered" empty-text="没有本地行情。请选择外部导入，或开启 CTP 持续采集。">
   <el-table-column prop="product" label="品种" width="115"/><el-table-column prop="code" label="合约" min-width="150"/><el-table-column label="类型" width="95"><template #default="{row}">{{periods[row.period]}}</template></el-table-column>
   <el-table-column label="实际覆盖时间" min-width="240"><template #default="{row}">{{stamp(row.start)}} — {{stamp(row.end)}}<small v-if="row.error">{{row.error}}</small></template></el-table-column>
   <el-table-column label="条数 / 体积" min-width="125"><template #default="{row}">{{row.rows?.toLocaleString()||0}}<small>{{bytes(row.bytes)}} · {{row.formats.join(' / ')}}</small></template></el-table-column>
   <el-table-column prop="profileid" label="环境" width="110"/><el-table-column label="操作" min-width="180"><template #default="{row}"><el-button link @click="openImport(row)">补充数据</el-button><el-button link type="danger" @click="previewDelete(row)">删除历史</el-button><small v-if="row.delete_reason">{{row.delete_reason}}</small></template></el-table-column>
  </el-table>
  <p class="history-note">Tick 是接收的行情快照。K 线不能恢复真实盘口和成交先后。只有 K 线时采用收盘后事件模型，不把合成事件称为真实 Tick。</p>
  <el-alert v-if="lastImport" :title="lastImport" type="success" :closable="false"/>
  <el-dialog v-model="importing" title="导入外部历史行情" width="640px">
   <el-form label-position="top"><el-form-item label="目标组合环境"><el-select v-model="form.groupid" @change="loadProviders(false)"><el-option v-for="group in groups" :key="group.id" :value="group.id" :label="group.name||group.id"/></el-select></el-form-item>
    <el-form-item label="具体合约"><el-input v-model="form.code" placeholder="如 SHFE.rb.2701"/></el-form-item>
    <el-form-item label="数据源"><el-select v-model="form.provider" aria-label="历史外部数据源"><el-option v-for="p in providers" :key="p.id" :value="p.id" :label="p.label"/></el-select><small>{{selectedProvider?.reason}}</small></el-form-item>
    <el-form-item label="数据类型"><el-select v-model="form.period"><el-option value="tick" label="真实 Tick（当前外部源不可补）"/><el-option value="m1" label="1 分钟"/><el-option value="m5" label="5 分钟"/><el-option value="d" label="日线"/></el-select></el-form-item>
    <el-form-item label="交易日起止（每次最多 31 天）"><el-date-picker v-model="form.dates" type="daterange" value-format="YYYYMMDD" start-placeholder="开始交易日" end-placeholder="结束交易日"/></el-form-item>
    <el-checkbox v-model="restartCollector">允许写入时短暂停采，完成后恢复原采集进程</el-checkbox>
    <el-alert v-if="form.period==='tick'" title="AkShare 与这里接入的 Tushare 接口不提供历史期货 Tick。请继续 CTP 采集；不会用 K 线伪造补齐 Tick。" type="warning" :closable="false"/>
    <el-alert v-else-if="!importAvailable" :title="providerReason" type="warning" :closable="false"/>
    <small>私有 Token 只从服务器凭据文件读取，不传给游客。分钟线仅补平台仍保留的日期；没有返回就报告缺失。</small>
   </el-form>
   <el-table v-if="jobs.length" :data="jobs.slice(0,5)" size="small"><el-table-column prop="code" label="合约"/><el-table-column label="类型 / 请求日期" min-width="160"><template #default="{row}">{{periods[row.period]}}<small>{{stamp(row.start_date)}} — {{stamp(row.end_date)}}</small></template></el-table-column><el-table-column prop="status" label="状态"/><el-table-column label="结果" min-width="190"><template #default="{row}">{{row.error||row.message}}<small v-if="row.status==='completed'">平台返回 {{row.received_rows||0}} 条，新增 {{row.rows||0}} 条；目录共 {{row.total_rows||0}} 条</small></template></el-table-column></el-table>
   <template #footer><el-button @click="importing=false">关闭</el-button><el-button v-if="form.provider==='tushare'" @click="loadProviders(true)">检查接口权限</el-button><el-button type="primary" :loading="submitting" @click="submitImport">导入所选日期</el-button></template>
  </el-dialog>
  <el-dialog v-model="deleting" title="确认删除历史数据" width="620px"><p>{{deletion?.dataset?.code}} · {{periods[deletion?.dataset?.period]}}</p><el-alert :title="deletion?.message||''" type="warning" :closable="false"/><el-table :data="deletion?.files||[]"><el-table-column prop="name" label="将删除的文件"/><el-table-column label="体积" width="100"><template #default="{row}">{{bytes(row.bytes)}}</template></el-table-column></el-table><template #footer><el-button @click="deleting=false">取消</el-button><el-button type="danger" :loading="submitting" @click="deleteHistory">确认删除</el-button></template></el-dialog>
 </section>
</template>
<script>
import {useSessionStore} from '@/store.js'
export default {
 name:'HistoryManager',
 data:()=>({entries:[],groups:[],loading:false,submitting:false,restartCollector:false,search:'',period:'',periods:{tick:'Tick',m1:'1 分钟',m5:'5 分钟',d:'日线'},importing:false,deleting:false,deletion:null,providers:[],jobs:[],pendingJobs:[],lastImport:'',form:{groupid:'',code:'',provider:'akshare',period:'m1',dates:[]}}),
 computed:{guest(){return useSessionStore().cache.userinfo.role==='guest'},filtered(){const term=this.search.trim().toLowerCase();return this.entries.filter(row=>(!this.period||row.period===this.period)&&(!term||(row.code+' '+row.product+' '+row.label).toLowerCase().includes(term)))},selectedProvider(){return this.providers.find(p=>p.id===this.form.provider)},importAvailable(){return this.form.period!=='tick'&&this.selectedProvider?.periods?.some(p=>p.value===this.form.period&&p.available)},providerReason(){return this.selectedProvider?.periods?.find(p=>p.value===this.form.period)?.reason||this.selectedProvider?.reason||'请先选择组合环境'}},
 methods:{
  allow(){if(this.guest){this.$message.warning('游客模式不支持操作');return false}return true},
  async request(path,payload={}){const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const value=await response.json();if(!response.ok||value.result<0)throw new Error(value.message||'数据操作失败');return value},
  stamp(value){if(!value)return'—';const s=String(value);return s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8)+(s.length>8?' '+s.slice(8,10)+':'+s.slice(10,12):'')},
  bytes(value){return value>=1073741824?(value/1073741824).toFixed(2)+' GB':value>=1048576?(value/1048576).toFixed(2)+' MB':(value/1024).toFixed(1)+' KB'},
  async refresh(force=true){this.loading=true;try{const value=await this.request('/mgr/history/catalog',{refresh:force});this.entries=value.entries||[]}catch(error){this.$message.error(error.message)}finally{this.loading=false}},
  async openImport(row){if(!this.allow())return;this.form={groupid:row?.groupid||this.groups[0]?.id||'',code:row?.code||'',provider:'akshare',period:row?.period==='tick'?'m1':row?.period||'m1',dates:[]};this.importing=true;await this.loadProviders()},
  async loadProviders(refresh=false){if(!this.form.groupid)return;try{const value=await this.request('/mgr/market/providers',{groupid:this.form.groupid,refresh});this.providers=value.providers||[];await this.loadJobs()}catch(error){this.$message.error(error.message)}},
  async loadJobs(){
   if(this._jobsLoading)return;this._jobsLoading=true
   try{
    const groupids=[...new Set([...(this.importing?[this.form.groupid]:[]),...this.pendingJobs.map(row=>row.groupid)])].filter(Boolean)
    let completed=false
    for(const groupid of groupids){
     const value=await this.request('/mgr/market/jobs',{groupid});const rows=value.jobs||[]
     if(groupid===this.form.groupid)this.jobs=rows
     for(const job of rows){
      const index=this.pendingJobs.findIndex(row=>row.id===job.id)
      if(index<0||!['completed','failed'].includes(job.status))continue
      this.pendingJobs.splice(index,1)
      if(job.status==='completed'){
       completed=true;this.lastImport=job.code+' · '+this.periods[job.period]+'导入完成，新增 '+(job.rows||0)+' 条。其他数据类型保持原样。'
      }else this.$message.error(job.error||'历史导入失败')
     }
    }
    if(completed)await this.refresh()
   }catch(error){this.$message.error(error.message)}finally{this._jobsLoading=false}
  },
  async submitImport(){if(!this.allow())return;if(!this.importAvailable){this.$message.warning(this.providerReason);return}if(!this.form.code||this.form.dates?.length!==2){this.$message.warning('请填写具体合约与起止交易日');return}this.submitting=true;try{const value=await this.request('/mgr/market/backfill',{groupid:this.form.groupid,code:this.form.code,provider:this.form.provider,period:this.form.period,start_date:Number(this.form.dates[0]),end_date:Number(this.form.dates[1]),restart_collector:this.restartCollector});this.pendingJobs.push({id:value.task.id,groupid:this.form.groupid});this.lastImport='';this.$message.success(value.message);await this.loadJobs()}catch(error){this.$message.error(error.message)}finally{this.submitting=false}},
  async previewDelete(row){if(!this.allow())return;try{this.deletion=await this.request('/mgr/history/delete_preview',{id:row.id});this.deleting=true}catch(error){this.$message.error(error.message)}},
  async deleteHistory(){if(!this.allow()||!this.deletion)return;this.submitting=true;try{const value=await this.request('/mgr/history/delete',{id:this.deletion.dataset.id,confirmation:this.deletion.confirmation});this.$message.success(value.message);this.deleting=false;await this.refresh()}catch(error){this.$message.error(error.message)}finally{this.submitting=false}}
 },
 async mounted(){await this.refresh(false);try{const value=await this.request('/mgr/qrygrp');this.groups=value.groups||[]}catch(error){this.$message.error(error.message)}this._poll=setInterval(()=>{if((this.importing||this.pendingJobs.length)&&!this.guest)this.loadJobs()},3000)},beforeUnmount(){clearInterval(this._poll)}
}
</script>
<style scoped>
.history-manager{height:100%;overflow:auto;box-sizing:border-box;padding:28px;color:var(--wt-text)}header{display:flex;align-items:center;gap:12px;margin-bottom:22px}header>div{margin-right:auto}h1{font-size:20px;font-weight:550;margin:0 0 10px}p,small,.history-note{font-size:12px;color:var(--wt-muted);line-height:1.8}small{display:block}.history-filters{display:flex;align-items:center;gap:14px;margin:22px 0}.history-filters .el-input{width:260px}.history-filters .el-select{width:150px}.history-filters span{font-size:12px;color:var(--wt-muted)}.history-note{margin-top:24px}.el-form-item .el-select{width:100%}
</style>
