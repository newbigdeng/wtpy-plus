<template>
 <section class="execution-quality" aria-label="交易执行质量">
  <template v-if="quality">
   <section v-if="telemetry?.available" aria-label="原生送单遥测">
    <strong>送单后的真实回报</strong>
    <div class="quality-summary">
     <div><small>遥测送单</small><b>{{telemetry.summary.submitted_orders}} 单</b><span>拒单 {{telemetry.summary.rejected}} · 未完结 {{telemetry.summary.open}}</span></div>
     <div><small>按量成交率</small><b>{{percent(telemetry.summary.volume_fill_rate)}}</b><span>{{telemetry.summary.filled_volume}} / {{telemetry.summary.submitted_volume}} 手</span></div>
    </div>
    <p>滑点对比送单前本机对手价。延迟使用同进程单调时钟。</p>
    <el-table :data="telemetry.orders||[]" max-height="360" empty-text="遥测启用后暂无送单">
     <el-table-column label="送单时间（北京）" min-width="165"><template #default="{row}">{{time(row.wall_time_ns/1000000)}}</template></el-table-column>
     <el-table-column prop="code" label="合约" min-width="110"/>
     <el-table-column label="方向" width="65"><template #default="{row}">{{row.side==='buy'?'买':'卖'}}</template></el-table-column>
     <el-table-column label="成交 / 委托" width="110"><template #default="{row}">{{row.filled}} / {{row.total}}</template></el-table-column>
     <el-table-column label="状态" width="85"><template #default="{row}">{{status(row.status)}}</template></el-table-column>
     <el-table-column label="对手价" width="95"><template #default="{row}"><span :title="'报价时间 '+time(row.benchmark_quote?.updated_at)">{{number(row.benchmark_price)}}</span></template></el-table-column>
     <el-table-column label="滑点（价格）" width="120"><template #default="{row}">{{number(row.arrival_slippage)}}</template></el-table-column>
     <el-table-column label="送单→首个委托回报" min-width="170"><template #default="{row}">{{milliseconds(row.submit_to_ack_ms)}}</template></el-table-column>
     <el-table-column label="送单→首成" min-width="125"><template #default="{row}">{{milliseconds(row.submit_to_first_fill_ms)}}</template></el-table-column>
     <el-table-column label="送单→全成" min-width="125"><template #default="{row}">{{milliseconds(row.submit_to_last_fill_ms)}}</template></el-table-column>
     <el-table-column label="撤单→回报" min-width="125"><template #default="{row}">{{milliseconds(row.cancel_to_ack_ms)}}</template></el-table-column>
    </el-table>
    <details><summary>每笔遥测成交</summary><el-table :data="telemetry.trades||[]" max-height="280"><el-table-column prop="code" label="合约" min-width="110"/><el-table-column prop="tradeid" label="成交编号" width="110"/><el-table-column prop="qty" label="数量" width="70"/><el-table-column prop="price" label="成交价" width="100"/><el-table-column label="滑点（价格）" width="130"><template #default="{row}">{{number(row.arrival_slippage)}}</template></el-table-column><el-table-column label="送单→成交" min-width="130"><template #default="{row}">{{milliseconds(row.submit_to_fill_ms)}}</template></el-table-column></el-table></details>
    <details><summary>遥测的时间与价格</summary><p v-for="(value,key) in telemetry.definitions" :key="key">{{value}}</p></details>
   </section>
   <p v-else>原生送单遥测尚未启用。历史账簿保留如下。</p>
   <strong>历史通道账簿</strong>
   <p v-if="summary.sample_start_date">{{summary.sample_start_date}} 至 {{summary.sample_end_date}}，通道全部已存终态单。</p>
   <div class="quality-summary">
    <div><small>账簿终态委托</small><b>{{summary.terminal_orders}} 单</b><span>全成 {{summary.filled}} · 撤单 {{summary.canceled}}</span></div>
    <div><small>终态单按量成交率</small><b>{{percent(summary.terminal_volume_fill_rate)}}</b><span>{{summary.terminal_filled_volume}} / {{summary.terminal_order_volume}} 手</span></div>
    <div><small>终态单全部成交比例</small><b>{{percent(summary.terminal_full_order_rate)}}</b><span>全部成交单 / 终态单</span></div>
   </div>
   <p>买入价减限价，卖出限价减成交价。正值更差；负值更好。</p>
   <p>柜台时间精确到秒。“同一秒”不等于零延迟。</p>
   <el-alert v-if="summary.unmatched_fills||summary.unreconciled_orders||summary.invalid_rows" type="warning" :closable="false" :title="`未关联成交 ${summary.unmatched_fills} 笔；数量不符 ${summary.unreconciled_orders} 单；无效记录 ${summary.invalid_rows} 条`"/>
   <el-table :data="quality.orders||[]" max-height="360" empty-text="账簿暂无终态委托">
    <el-table-column label="报单时间（北京）" min-width="160"><template #default="{row}">{{time(row.insert_time)}}</template></el-table-column>
    <el-table-column prop="channel" label="通道" width="95"/>
    <el-table-column prop="code" label="合约" min-width="130"/>
    <el-table-column label="操作" width="75"><template #default="{row}">{{action(row.action)}}</template></el-table-column>
    <el-table-column label="成交 / 委托" width="110"><template #default="{row}">{{row.filled}} / {{row.total}}</template></el-table-column>
    <el-table-column label="状态" width="95"><template #default="{row}">{{status(row.status)}}</template></el-table-column>
    <el-table-column label="成交均价" width="105"><template #default="{row}">{{number(row.vwap)}}</template></el-table-column>
    <el-table-column label="限价偏离（跳）" width="135"><template #default="{row}"><span :title="`价格偏离 ${number(row.limit_deviation)}；非送单时行情滑点`">{{number(row.limit_deviation_ticks)}}</span></template></el-table-column>
    <el-table-column label="柜台报单→首成" min-width="150"><template #default="{row}">{{duration(row.exchange_first_fill_ms)}}</template></el-table-column>
    <el-table-column label="柜台报单→末成" min-width="150"><template #default="{row}">{{duration(row.exchange_last_fill_ms)}}</template></el-table-column>
    <el-table-column label="成交核对" width="110"><template #default="{row}">{{row.reconciled?'数量一致':'数量不符'}}</template></el-table-column>
   </el-table>
   <details><summary>每笔成交</summary><el-table :data="quality.trades||[]" max-height="320">
    <el-table-column label="成交时间（北京）" min-width="165"><template #default="{row}">{{time(row.trade_time)}}</template></el-table-column>
    <el-table-column prop="code" label="合约" min-width="130"/><el-table-column prop="tradeid" label="成交编号" width="110"/>
    <el-table-column prop="volume" label="成交量" width="85"/><el-table-column prop="price" label="成交价" width="95"/>
    <el-table-column label="限价偏离（跳）" width="135"><template #default="{row}">{{number(row.limit_deviation_ticks)}}</template></el-table-column>
    <el-table-column label="柜台报单→成交" min-width="150"><template #default="{row}">{{duration(row.exchange_fill_ms)}}</template></el-table-column>
   </el-table></details>
   <details><summary>这些数值怎么算</summary><p v-for="(value,key) in quality.definitions" :key="key">{{value}}</p><p>通道级统计包含外部委托。未按策略归属过滤。明细最多 {{quality.detail_limit}} 条，汇总使用全部账簿。</p><p>未完结单、完整拒单、送单时行情滑点、本地回报延迟：原账簿未提供。</p></details>
  </template>
  <p v-else>等待读取通道执行账簿。</p>
 </section>
</template>
<script>
import {tradeAction,tradeTime} from '@/js/market-trades.js'
export default {
 name:'ExecutionQualityPanel',props:{quality:{type:Object,default:null}},
 computed:{summary(){return this.quality?.summary||{}},telemetry(){return this.quality?.telemetry}},
 methods:{action:tradeAction,time(value){return value?tradeTime(value):'未提供'},
  number(value){return value==null?'未提供':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:3})},
  percent(value){return value==null?'未提供':(value*100).toFixed(1)+'%'},
  status(value){return {filled:'全部成交',canceled:'已撤单',rejected:'已拒单',open:'未完结',terminal_unclassified:'其他终态'}[value]||value},
  milliseconds(value){return value==null?'未提供':this.number(value)+' ms'},
  duration(value){return value==null?'未提供':value===0?'同一秒':(value/1000)+' 秒（秒精度）'}}
}
</script>
<style scoped>
.execution-quality{padding:10px;min-width:0}.quality-summary{display:flex;gap:12px;flex-wrap:wrap}.quality-summary>div{display:flex;flex:1;min-width:140px;flex-direction:column;padding:9px;background:var(--el-fill-color-light);border-radius:6px}.quality-summary b{font-size:20px}.quality-summary small,.quality-summary span{color:var(--el-text-color-secondary);font-size:12px}.execution-quality p{font-size:12px;color:var(--el-text-color-secondary);margin:7px 0}.execution-quality details{margin-top:10px}.execution-quality summary{cursor:pointer;font-size:13px}
</style>
