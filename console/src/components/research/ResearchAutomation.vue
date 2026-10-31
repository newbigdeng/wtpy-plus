<template>
 <section class="factor-auto" aria-label="因子自动研究">
  <div class="auto-title"><div><strong>自动研究</strong><p>从研报核对公式，检验后入库；策略应用另过前向验证。</p></div><small>{{ checkedAt }}</small></div>
  <div class="auto-counts"><span>已筛研报 <b>{{ data.counts?.screened || 0 }}</b></span><span>研究任务 <b>{{ data.counts?.jobs || 0 }}</b></span><span>因子版本 <b>{{ data.counts?.versions || 0 }}</b></span><span>研究通过 <b>{{ data.counts?.research_accepted || 0 }}</b></span></div>
  <details><summary>查看进度、候选与失效观察</summary>
   <p v-if="!data.jobs?.length" class="auto-empty">后台会筛选新研报。没有明确公式或必要数据时，会保留原因并等待。</p>
   <div v-for="job in data.jobs || []" :key="job.id" class="auto-row"><div><strong>{{ job.spec?.name || '公式核对' }}</strong><small>{{ frequency(job.spec?.frequency) }}</small></div><span>{{ label(job.status) }}</span><small>{{ reasons(job.reasons) }}</small></div>
   <div v-for="version in data.versions || []" :key="version.id" class="auto-row"><div><strong>{{ version.name }} · v{{ version.version }}</strong><small>{{ version.status === 'research_accepted' ? '策略候选' : '研究归档' }}</small></div><span>{{ label(version.promotion?.status || version.status) }}</span><small>{{ version.status === 'research_rejected' ? '该版本仅归档，不进入交易' : waitReason(version.automation_wait_reason || version.promotion?.reason) }}</small><details class="auto-definition"><summary>查看公式与来源</summary><p><code>{{ version.selected_candidate?.expression }}</code></p><p>回看 {{ version.selected_candidate?.lookback }} 根，评价 {{ version.selected_candidate?.horizon }} 根。</p><p v-if="version.source_refs?.[0]"><a :href="version.source_refs[0].url" target="_blank" rel="noopener noreferrer">打开研报原文</a> · {{ version.source_refs[0].section }}<span v-if="version.source_refs[0].pdf_page"> · PDF第{{ version.source_refs[0].pdf_page }}页</span></p></details></div>
   <div v-for="item in data.monitors || []" :key="item.id" class="auto-row"><div><strong>持续观察</strong><small>{{ item.rolling_window_bars }}根{{ frequency(item.frequency) }} · {{ item.exposure === 'applied_manifest' ? '已参与策略' : '研究观察' }}</small></div><span>{{ label(item.lifecycle_request) }}</span><small>{{ reasons(item.reasons) || '保留观察，不因短期变化直接调整交易' }}</small></div>
   <details v-if="data.screens?.length"><summary>研报筛选记录</summary><p v-for="item in data.screens" :key="item.report_id" class="auto-note">{{ item.report_title || '研报' }}：{{ item.reason || label(item.status) }}</p></details>
   <p class="auto-note">历史不足会自动尝试补入数据湖。当前原生验证仍要求20个后续完整交易日和60笔闭合交易，并保留成本压力、风险和安全切换检查。</p>
  </details>
 </section>
</template>

<script>
const labels = { ready:'等待复现', waiting:'等待原文核对', waiting_data:'等待历史数据', running:'隔离检验中', failed:'执行失败，保留输入', rejected:'公式不符合要求', research_accepted:'研究检验通过', research_rejected:'研究检验未通过', collecting:'积累前向样本', deferred:'等待验证条件', skipped:'暂不创建候选', passed:'等待安全应用', applied:'已安全应用', rolled_back:'已回退', stale_baseline:'基线已变化', expired:'候选已过期', observe:'继续观察', continue_observation:'持续观察', deactivation_review:'进入停用候选验证' }
const messages = { missing_required_bar_inputs:'缺少必要行情字段', insufficient_history:'历史不足', closed_bar_snapshot_required:'等待闭合行情', independent_formula_review_required:'等待独立公式审核', source_formula_not_mechanically_verified:'原文公式尚不能可靠转换', full_source_text_required:'等待完整研报正文', isolated_reproduction_failed:'复现失败，等待重试', validation_insufficient_labels:'验证样本不足', test_insufficient_labels:'保留测试样本不足', validation_weak_or_reversed_rank_ic:'验证期预测关系不足', test_weak_or_reversed_rank_ic:'保留测试预测关系不足', validation_nonpositive_after_cost:'验证期扣成本后未改善', test_nonpositive_after_cost:'保留测试扣成本后未改善', rolling_ic_weak_or_reversed:'滚动预测关系走弱', rolling_nonpositive_after_cost:'滚动扣成本表现走弱', rolling_low_coverage:'近期数据覆盖不足', internal_factor_redundancy:'与已有因子信息重复', insufficient_monitor_labels:'监测样本不足' }
export default {
 props:{data:{type:Object,default:()=>({})}},
 computed:{checkedAt(){const stamp=this.data.cycle?.[0]?.checked_at;return stamp ? '更新于 '+new Date(stamp).toLocaleString('zh-CN',{hour12:false}) : '等待后台首次运行'}},
 methods:{waitReason(value){if(!value)return '研究通过与交易准入分别判断';if(/funds|account|资金/i.test(value))return '等待有效的账户查询结果';if(/history|bars|data|数据|历史/i.test(value))return '等待完整历史与预热数据';if(/cohort|collecting|forward|window/i.test(value))return '等待前向验证与独立样本';if(/baseline|revision/i.test(value))return '当前策略版本已变化，等待重新核对';return '等待后台核对，原策略保持运行'},label(value){return labels[value] || '等待下一步核对'},frequency(value){return {d:'日线',m1:'分钟线',m5:'五分钟线'}[value] || ''},reasons(values){return (values || []).map(x=>messages[x] || '有待核对项，后台保留了具体原因').join('；')}}
}
</script>

<style scoped>
.factor-auto{border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin-bottom:18px;background:#fff}.auto-title{display:flex;justify-content:space-between;gap:16px}.auto-title p,.auto-title small,.auto-note,.auto-empty{color:#667085;font-size:13px;line-height:1.7}.auto-title p{margin:5px 0 10px}.auto-counts{display:flex;gap:22px;flex-wrap:wrap;font-size:13px;margin-bottom:12px;color:#667085}.auto-counts b{color:#202938;margin-left:6px}summary{cursor:pointer;color:#475467;font-size:13px}.auto-row{display:grid;grid-template-columns:minmax(150px,1fr) 150px minmax(180px,1fr);gap:16px;padding:12px 0;border-bottom:1px solid #f0f1f4;font-size:13px}.auto-row strong,.auto-row small{display:block}.auto-row small{color:#667085;line-height:1.6;overflow-wrap:anywhere}.auto-note{margin-bottom:0}.auto-definition{grid-column:1/-1}.auto-definition p{color:#667085;margin:8px 0;overflow-wrap:anywhere}@media(max-width:700px){.auto-title{display:block}.auto-row{grid-template-columns:1fr}.auto-counts{gap:12px}}
</style>
