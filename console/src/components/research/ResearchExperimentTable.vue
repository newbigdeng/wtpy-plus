<template>
 <el-table :data="rows" class="research-experiment-table" empty-text="还没有实验，先保存研究并配置一次回测。">
  <el-table-column label="实验" min-width="160"><template #default="{row}"><strong>{{shortId(row.id)}}</strong><small>项目 v{{row.project_version||1}} · {{time(row.created_at)}}</small></template></el-table-column>
  <el-table-column label="行情与日期" min-width="220"><template #default="{row}"><span>{{row.code||row.dataset}}</span><small>{{stamp(row.start)}} — {{stamp(row.end)}} · {{period(row.period)}}</small></template></el-table-column>
  <el-table-column label="状态" min-width="170"><template #default="{row}"><span :class="'research-status '+row.status">{{status(row.status)}}</span><el-progress v-if="['running','queued','preparing'].includes(row.status)" :percentage="Math.min(100,Math.max(0,Number(row.progress)||0))" :stroke-width="4"/><small v-if="row.error" class="research-task-error">{{row.error}}</small></template></el-table-column>
  <el-table-column label="成交" width="80"><template #default="{row}">{{row.metrics?.trade_count??'—'}}</template></el-table-column>
  <el-table-column label="操作" width="155" fixed="right"><template #default="{row}"><el-button text @click="$emit('open',row)" :aria-label="'查看研究实验 '+row.id">{{row.status==='completed'?'阅读成果':'查看详情'}}</el-button><router-link v-if="row.status==='completed'" :to="{name:'run-report',query:{asset:'futures',mode:'research',task:row.id}}">报告</router-link><el-button v-if="canEdit&&['running','queued','preparing'].includes(row.status)" text type="danger" @click="$emit('cancel',row)">取消</el-button></template></el-table-column>
 </el-table>
</template>
<script>
export default {
 name:'ResearchExperimentTable',props:{rows:{type:Array,default:()=>[]},canEdit:Boolean},emits:['open','cancel'],
 methods:{shortId(value){return String(value||'').slice(0,12)},status(value){return {queued:'排队中',preparing:'准备数据',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消',canceled:'已取消',interrupted:'已中断',stopped:'已停止'}[value]||value},period(value){return {tick:'Tick',m1:'一分钟',m5:'五分钟',d:'日线'}[value]||value||'—'},stamp(value){const text=String(value||'');return text.length>=8?text.slice(0,4)+'-'+text.slice(4,6)+'-'+text.slice(6,8):'—'},time(value){if(!value)return'—';const number=Number(value);return Number.isFinite(number)?new Date(number<1e12?number*1000:number).toLocaleString('zh-CN',{hour12:false}):String(value).replace('T',' ').slice(0,19)}}
}
</script>
<style scoped>
.research-experiment-table{width:100%;font-size:12px}.research-experiment-table strong{font-weight:500}.research-experiment-table small{display:block;font-size:11px;color:var(--wt-muted);line-height:1.7;margin-top:4px;overflow-wrap:anywhere}.research-status{font-size:12px}.research-status.completed{color:var(--wt-positive,var(--wt-text))}.research-status.failed,.research-experiment-table .research-task-error{color:var(--wt-danger)}.research-experiment-table :deep(.el-progress){margin-top:7px}.research-experiment-table :deep(.el-button){font-size:12px;padding:4px}.research-experiment-table :deep(.el-button+.el-button){margin-left:2px}
</style>
