<template>
 <section class="source-summary" aria-label="原文核心内容">
  <header><div><small>{{['abstract','excerpt'].includes(summary?.source_ref?.body_scope)?'摘要 / 摘录提炼':'原文提炼'}}</small><h3>这份资料讲了什么</h3></div><span v-if="summary" class="summary-version">摘要 v{{summary.version}}<template v-if="summary.status==='stale'"> · 原文已更新</template></span><el-button v-if="canRequest" text :loading="pending" @click="$emit('request')">{{summary?'重新提炼':'提炼核心内容'}}</el-button></header>
  <p v-if="summary&&job?.status==='failed'" class="source-muted">上次提炼未完成：{{job.error}}</p>
  <p v-if="summary?.reading_scope==='selected_excerpts'" class="source-muted">长文按原文片段提炼；引用可回读，不表示已经通读全文。</p>
  <template v-if="summary">
   <div class="source-overview"><article v-for="item in overview" :key="item.key"><small>{{item.label}}</small><p>{{summary.overview?.[item.key]?.text||'原文未明确说明。'}}</p><button v-for="id in refs(summary.overview?.[item.key])" :key="id" class="source-cite" @click="showAnchor(id)">原文 {{id}}</button></article></div>
   <template v-if="!summary.preview">
    <div class="source-formulas"><h4>原文公式</h4><p v-if="!summary.formulas?.length" class="source-muted">{{summary.formula_state==='unreadable'?'公式未能从原文中清楚提取，请查看原件。':'可读文本中未提取明确公式，未补写。'}}</p><article v-for="(formula,index) in summary.formulas" :key="index"><pre>{{formula.expression}}</pre><p v-for="(symbol,j) in formula.symbols" :key="j">{{symbol.text}}</p><button v-for="id in refs(formula)" :key="id" class="source-cite" @click="showAnchor(id)">原文 {{id}}</button></article></div>
    <details class="source-details"><summary>字段、时点与检验条件</summary><div class="source-facts"><article v-for="section in facts" :key="section.key"><h4>{{section.label}}</h4><template v-if="summary[section.key]?.length"><p v-for="(claim,index) in summary[section.key]" :key="index">{{claim.text}} <button v-for="id in refs(claim)" :key="id" class="source-cite" @click="showAnchor(id)">{{id}}</button></p></template><p v-else class="source-muted">原文未明确说明。</p></article></div></details>
    <div class="source-reproduction"><strong>{{reproductionLabel}}</strong><p>{{summary.reproduction?.reason?.text||'是否可复现，仍需核对公式和实际输入。'}}</p><button v-for="id in refs(summary.reproduction?.reason)" :key="id" class="source-cite" @click="showAnchor(id)">原文 {{id}}</button></div>
    <details class="source-details" :open="refsOpen"><summary>原文页码与引用</summary><article v-for="anchor in summary.anchors" :key="anchor.id" :data-anchor="anchor.id" :class="['source-anchor',{selected:anchor.id===selectedAnchor}]"><small>{{anchor.id}} · {{anchor.pdf_page?'PDF 第 '+anchor.pdf_page+' 页':'正文位置 '+anchor.text_start}}</small><blockquote>{{anchor.exact_quote}}</blockquote></article></details>
   </template>
   <p v-else class="source-muted">打开资料可查看原文公式、条件与引用。</p>
  </template>
  <p v-else class="source-muted">{{pending?'核心摘要已排队，完成后会自动显示。':job?.status==='waiting_text'?(job.error||'原件没有可读正文，未生成摘要。'):job?.status==='failed'?((job.attempts||0)>=3?'自动重试已停止，可修正问题后重新提炼：':'等待自动重试：')+job.error:'先阅读原文；核心摘要尚未生成。'}}</p>
 </section>
</template>

<script>
export default {
 name:'ResearchSourceSummary',props:{summary:Object,job:Object,canRequest:Boolean},emits:['request'],
 data:()=>({refsOpen:false,selectedAnchor:'',overview:[{key:'question',label:'研究问题'},{key:'conclusion',label:'核心结论'},{key:'method',label:'研究方法'}],facts:[{key:'fields_and_series',label:'字段与合约序列'},{key:'frequency_windows',label:'频率与窗口'},{key:'available_timing',label:'数据何时可用'},{key:'sample_costs',label:'样本与成本'},{key:'limitations',label:'原文限制'}]}),
 computed:{pending(){return ['queued','running'].includes(this.job?.status)},reproductionLabel(){return {candidate:'可进入公式核对',needs_inputs:'需要补齐输入',unsupported:'现有流程不支持',not_assessed:'尚未进入复现检验'}[this.summary?.reproduction?.status]||'复现状态待核对'}},
 methods:{refs(claim){return this.summary?.preview?[]:claim?.anchor_ids||[]},async showAnchor(id){this.selectedAnchor=id;this.refsOpen=true;await this.$nextTick();[...this.$el.querySelectorAll('[data-anchor]')].find(node=>node.dataset.anchor===id)?.scrollIntoView({block:'nearest',behavior:'smooth'})}}
}
</script>

<style scoped>
.source-summary{border:1px solid var(--wt-line);border-radius:8px;padding:20px;background:var(--wt-surface);margin:16px 0}.source-summary header{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.source-summary header>div{flex:1}.source-summary h3{margin:6px 0 16px;font-size:17px;font-weight:550}.source-summary small,.source-muted,.summary-version{color:var(--wt-muted);font-size:12px}.source-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.source-overview article{min-width:0}.source-overview p{margin:8px 0;line-height:1.85;font-size:13px;white-space:pre-wrap}.source-cite{border:0;background:transparent;color:var(--wt-accent,var(--wt-text));font:inherit;font-size:11px;text-decoration:underline;text-underline-offset:3px;cursor:pointer;padding:3px 5px}.source-summary h4{font-size:13px;font-weight:550;margin:16px 0 8px}.source-summary p{line-height:1.8;overflow-wrap:anywhere}.source-formulas pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--wt-matte);border-left:2px solid var(--wt-line);padding:14px;font:13px/1.9 Consolas,monospace}.source-formulas article>p{font-size:12px}.source-details{border-top:1px solid var(--wt-line);margin-top:18px;padding-top:15px}.source-details summary{cursor:pointer;font-size:12px}.source-facts{display:grid;grid-template-columns:1fr 1fr;gap:6px 24px}.source-facts p{font-size:12px}.source-reproduction{margin-top:18px;padding:14px 16px;background:var(--wt-matte);border-radius:5px;font-size:12px}.source-reproduction p{margin:7px 0}.source-anchor{padding:14px;margin-top:10px;border:1px solid transparent;border-radius:5px}.source-anchor.selected{border-color:var(--wt-line);background:var(--wt-matte)}.source-anchor blockquote{margin:9px 0 0;font-size:12px;line-height:1.85;white-space:pre-wrap;overflow-wrap:anywhere}.source-cite:focus-visible,summary:focus-visible{outline:2px solid var(--wt-text);outline-offset:3px}@media(max-width:850px){.source-overview{grid-template-columns:1fr;gap:12px}.source-facts{grid-template-columns:1fr}.source-summary{padding:16px}}
</style>
