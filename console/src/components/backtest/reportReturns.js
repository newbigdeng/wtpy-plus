export function returnsView(points,capital,sessions) {
  if (!(capital>0)) return {months:[], days:[], stats:{}}
  const endOfDay=new Map()
  for (const [time,equity] of points) {
    const day=String(time).replaceAll('-','').slice(0,8)
    if (/^\d{8}$/.test(day) && Number.isFinite(equity)) endOfDay.set(day,equity)
  }
  const months=new Map(), returns=[], days=[]
  let previous=capital, peak=capital, drawdown=0, valid=true
  for(const [day,equity] of [...endOfDay].sort(([a],[b])=>a.localeCompare(b))){
    const key=day.slice(0,6), m=months.get(key) || {key,base:previous}
    m.end=equity;months.set(key,m)
    days.push({day,equity,pnl:equity-previous,rate:previous>0?equity/previous-1:null,cumulative:equity/capital-1})
    if(previous>0) returns.push(equity/previous-1);else valid=false
    previous=equity;peak=Math.max(peak,equity);drawdown=Math.max(drawdown,1-equity/peak)
  }
  const n=returns.length, mean=n?returns.reduce((a,b)=>a+b,0)/n:0
  const volatility=valid && n>1?Math.sqrt(returns.reduce((s,x)=>s+(x-mean)**2,0)/(n-1)*sessions):null
  const annual=valid && n>1 && previous>0?(previous/capital)**(sessions/n)-1:null
  return {days, months:[...months.values()].map(m=>({key:m.key,value:m.base>0?m.end/m.base-1:null})),
    stats:{days:endOfDay.size,annual:Number.isFinite(annual)?annual:null,volatility,drawdown:n?drawdown:null,
           sharpe:volatility>0?mean*sessions/volatility:null,calmar:annual!=null && drawdown>0?annual/drawdown:null}}
}

export function reportTime(row,mode) {
  const value=String(row.time??'')
  if(!value)return '—'
  if(mode==='plus'){
    const day=String(row.date??''),clock=value.padStart(9,'0')
    if(/^\d{8}$/.test(day) && /^\d{9}$/.test(clock))return day.slice(0,4)+'-'+day.slice(4,6)+'-'+day.slice(6)+' '+clock.slice(0,2)+':'+clock.slice(2,4)+':'+clock.slice(4,6)+'.'+clock.slice(6)
  }
  if(/^\d{12}(?:\d{2}(?:\d{3})?)?$/.test(value))return value.slice(0,4)+'-'+value.slice(4,6)+'-'+value.slice(6,8)+' '+value.slice(8,10)+':'+value.slice(10,12)+(value.length>12?':'+value.slice(12,14):'')+(value.length===17?'.'+value.slice(14):'')
  return value
}
