import {format} from 'echarts'

const number=value=>value==null||!Number.isFinite(Number(value))?'—':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:4})
const text=value=>format.encodeHTML(String(value))

// Linked time cursors may include series from every grid. Keep the hovered grid.
export function marketTooltip(parameters,series,bars,panel){
 const rows=(Array.isArray(parameters)?parameters:[parameters]).filter(row=>{
  const item=series[row.seriesIndex]
  return item&&(item.xAxisIndex||0)===panel&&(panel!==0||item.name==='K线'||/^MA\d+$/.test(item.name))
 })
 if(!rows.length)return ''
 const bar=bars[rows[0].dataIndex]
 return [text(rows[0].axisValueLabel||bar?.time||''),...rows.map(row=>{
  // ECharts supplies the marker using this data point's actual visual colour.
  const marker=row.marker||''
  if(series[row.seriesIndex].type==='candlestick')return `${marker}K线  开 ${number(bar?.open)}  收 ${number(bar?.close)}  高 ${number(bar?.high)}  低 ${number(bar?.low)}`
  const value=row.data&&typeof row.data==='object'&&!Array.isArray(row.data)?row.data.value:row.data
  return `${marker}${text(row.seriesName)}: ${number(value)}`
 })].join('<br/>')
}
