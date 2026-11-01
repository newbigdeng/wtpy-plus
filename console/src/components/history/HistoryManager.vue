<template>
 <section class="history-manager">
  <header><div><h1>历史数据管理</h1><p>查看采集与导入的 WT 数据。各类型分别保存；导入日线只更新日线，不会补齐分钟线或 Tick。</p></div><el-button @click="openImport()">补充历史数据</el-button><el-button :loading="loading" @click="refresh()">刷新目录</el-button></header>
  <el-alert title="DSB 保存历史行情，DMB 是采集器实时文件。删除先预览，仅删除历史 DSB；采集、组合或回测运行时拒绝删除。" type="info" :closable="false"/>
  <MainReplayPanel preview @select-interval="chooseInterval"/>
  <div class="history-filters"><el-input v-model="search" placeholder="搜索品种或合约" clearable aria-label="历史合约搜索"/><el-select v-model="period" aria-label="历史数据类型"><el-option value="" label="全部类型"/><el-option v-for="(label,key) in periods" :key="key" :value="key" :label="label"/></el-select><span>{{filtered.length}} 个数据对象</span></div>
  <p class="history-note">日线排在同一合约最前。CNEquity 日线可按日期补数；分钟源只有近期窗口。数据湖准备完成后，还需确认任务已写入 WT。</p>
  <el-table :data="filtered" empty-text="没有本地行情。请选择外部导入，或开启 CTP 持续采集。">
   <el-table-column prop="product" label="品种" width="115"/><el-table-column prop="code" label="合约" min-width="150"/><el-table-column label="类型" width="95"><template #default="{row}">{{periods[row.period]}}</template></el-table-column>
   <el-table-column label="实际覆盖时间" min-width="240"><template #default="{row}">{{stamp(row.start)}} — {{stamp(row.end)}}<small v-if="row.error">{{row.error}}</small></template></el-table-column>
   <el-table-column label="条数 / 体积" min-width="125"><template #default="{row}">{{row.rows?.toLocaleString()||0}}<small>{{bytes(row.bytes)}} · {{row.formats.join(' / ')}}</small></template></el-table-column>
   <el-table-column prop="profileid" label="环境" width="110"/><el-table-column label="操作" min-width="180"><template #default="{row}"><el-button link @click="openImport(row)">补充数据</el-button><el-button link type="danger" @click="previewDelete(row)">删除历史</el-button><small v-if="row.delete_reason">{{row.delete_reason}}</small></template></el-table-column>
  </el-table>
  <p class="history-note">Tick 是接收的行情快照。K 线不能恢复真实盘口和成交先后。只有 K 线时采用收盘后事件模型，不把合成事件称为真实 Tick。</p>
  <el-dialog v-model="deleting" title="确认删除历史数据" width="620px"><p>{{deletion?.dataset?.code}} · {{periods[deletion?.dataset?.period]}}</p><el-alert :title="deletion?.message||''" type="warning" :closable="false"/><el-table :data="deletion?.files||[]"><el-table-column prop="name" label="将删除的文件"/><el-table-column label="体积" width="100"><template #default="{row}">{{bytes(row.bytes)}}</template></el-table-column></el-table><template #footer><el-button @click="deleting=false">取消</el-button><el-button type="danger" :loading="submitting" @click="deleteHistory">确认删除</el-button></template></el-dialog>
 </section>
</template>
<script>
import MainReplayPanel from "../backtest/MainReplayPanel.vue"
import {useSessionStore} from '@/store.js'
export default {
 components:{MainReplayPanel},
 name:'HistoryManager',
 data:()=>({entries:[],loading:false,submitting:false,restartCollector:false,search:'',period:'',periods:{tick:'Tick',m1:'1 分钟',m5:'5 分钟',d:'日线'},deleting:false,deletion:null}),
 computed:{guest(){return useSessionStore().cache.userinfo.role==='guest'},filtered(){const term=this.search.trim().toLowerCase(),order={d:0,m1:1,m5:2,tick:3};return this.entries.filter(row=>(!this.period||row.period===this.period)&&(!term||(row.code+' '+row.product+' '+row.label).toLowerCase().includes(term))).sort((a,b)=>a.code.localeCompare(b.code)||a.profileid.localeCompare(b.profileid)||order[a.period]-order[b.period])}},
 methods:{
  allow(){if(this.guest){this.$message.warning('游客模式不支持操作');return false}return true},
  async request(path,payload={}){const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const value=await response.json();if(!response.ok||value.result<0)throw new Error(value.message||'数据操作失败');return value},
  stamp(value){if(!value)return'—';const s=String(value);return s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8)+(s.length>8?' '+s.slice(8,10)+':'+s.slice(10,12):'')},
  bytes(value){return value>=1073741824?(value/1073741824).toFixed(2)+' GB':value>=1048576?(value/1048576).toFixed(2)+' MB':(value/1024).toFixed(1)+' KB'},
  async refresh(force=true){this.loading=true;try{const value=await this.request('/mgr/history/catalog',{refresh:force});this.entries=value.entries||[]}catch(error){this.$message.error(error.message)}finally{this.loading=false}},
  chooseInterval({product,segment}){this.openImport({code:product+"."+segment.month,period:"d",start:segment.start,end:segment.end})},
  openImport(row={}){this.$router.push({path:'/history',query:{asset:'futures',view:'backfill',group:row.groupid,code:row.code,period:row.period,start:row.start&&String(row.start).slice(0,8),end:row.end&&String(row.end).slice(0,8)}})},
  async previewDelete(row){if(!this.allow())return;try{this.deletion=await this.request('/mgr/history/delete_preview',{id:row.id});this.deleting=true}catch(error){this.$message.error(error.message)}},
  async deleteHistory(){if(!this.allow()||!this.deletion)return;this.submitting=true;try{const value=await this.request('/mgr/history/delete',{id:this.deletion.dataset.id,confirmation:this.deletion.confirmation});this.$message.success(value.message);this.deleting=false;await this.refresh()}catch(error){this.$message.error(error.message)}finally{this.submitting=false}}
 },
 mounted(){this.refresh(false)}
}
</script>
<style scoped>
.history-manager{height:100%;overflow:auto;box-sizing:border-box;padding:28px;color:var(--wt-text)}header{display:flex;align-items:center;gap:12px;margin-bottom:22px}header>div{margin-right:auto}h1{font-size:20px;font-weight:550;margin:0 0 10px}p,small,.history-note{font-size:12px;color:var(--wt-muted);line-height:1.8}small{display:block}.history-filters{display:flex;align-items:center;gap:14px;margin:22px 0}.history-filters .el-input{width:260px}.history-filters .el-select{width:150px}.history-filters span{font-size:12px;color:var(--wt-muted)}.history-note{margin-top:24px}.el-form-item .el-select{width:100%}
</style>
