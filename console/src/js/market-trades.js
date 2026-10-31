const actions={OL:['开多',true,true],OS:['开空',false,true],CL:['平多',true,false],CS:['平空',false,false],CNL:['平多',true,false],CNS:['平空',false,false],开多:['开多',true,true],开空:['开空',false,true],平多:['平多',true,false],平空:['平空',false,false]}
const pad=value=>String(value).padStart(2,'0')

export function tradeAction(value){return actions[String(value||'').trim().toUpperCase()]?.[0]||String(value||'—')}

function tradeTimestamp(value){
 const text=String(value||'').trim()
 if(/^\d{13}$/.test(text))return Number(text)
 const digits=text.replace(/[- :T.]/g,'')
 if(!/^20\d{10,15}$/.test(digits))return null
 return Date.UTC(Number(digits.slice(0,4)),Number(digits.slice(4,6))-1,Number(digits.slice(6,8)),Number(digits.slice(8,10))-8,Number(digits.slice(10,12)),Number(digits.slice(12,14)||0))
}

export function tradeTime(value){
 const timestamp=tradeTimestamp(value)
 if(timestamp===null)return '时间未知'
 const date=new Date(timestamp+8*3600000)
 return `${date.getUTCFullYear()}-${pad(date.getUTCMonth()+1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`
}

export function tradeBarIndex(trade,bars,period){
 const timestamp=tradeTimestamp(trade.time)
 if(timestamp===null)return -1
 if(period==='d'){
  // Daily bars need an exchange trading date, never a guessed next weekday.
  const day=trade.trading_date||trade.trading_day
  return day?bars.findIndex(bar=>String(bar.date||bar.time).slice(0,8)===String(day).replaceAll('-','')):-1
 }
 const span=(period==='m5'?5:1)*60000
 // WT minute bars carry their closing time. Breaks must not absorb old fills.
 return bars.findIndex(bar=>{const end=tradeTimestamp(bar.time);return end!==null&&timestamp>=end-span&&timestamp<end})
}

export function tradeMarkers(trades,bars,code,period,colors){
 return trades.filter(trade=>trade.code===code).flatMap(trade=>{
  const action=actions[String(trade.action||'').trim().toUpperCase()],index=tradeBarIndex(trade,bars,period)
  if(!action||index<0||trade.price==null||!Number.isFinite(Number(trade.price)))return []
  const [name,long,open]=action,color=long?colors.up:colors.down
  return [{name,coord:[index,Number(trade.price)],value:name,symbol:open?'triangle':'circle',symbolRotate:long?0:180,symbolSize:open?13:8,itemStyle:{color},label:{show:true,formatter:name,position:long?'bottom':'top',distance:5,color,fontSize:10,backgroundColor:colors.background,padding:[2,3],borderRadius:2},trade:{...trade,action:name,time_label:tradeTime(trade.time)}}]
 })
}
