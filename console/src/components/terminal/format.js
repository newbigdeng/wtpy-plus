const percentageMetrics=new Set(['累计收益','年化波动率','年化均值','最大回撤','单期 VaR','单期 CVaR','隐含波动率','内部收益率','修正内部收益率','平价固定利率','存续概率','预期损失率'])
export function formatValue(value){
 if(value==null)return '—'
 if(typeof value==='number')return new Intl.NumberFormat('zh-CN',{maximumFractionDigits:6}).format(value)
 return typeof value==='object'?JSON.stringify(value):String(value)
}
export function formatMetric(key,value){return value==null?'—':percentageMetrics.has(key)?(value*100).toFixed(2)+'%':formatValue(value)}
