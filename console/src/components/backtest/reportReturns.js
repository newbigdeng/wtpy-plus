export function returnsView(points,capital,sessions) {
  if (!(capital>0)) return {months:[], stats:{}}
  const endOfDay=new Map()
  for (const [time,equity] of points) {
    const day=String(time).replaceAll('-','').slice(0,8)
    if (/^\d{8}$/.test(day) && Number.isFinite(equity)) endOfDay.set(day,equity)
  }
  const months=new Map(), returns=[]
  let previous=capital, peak=capital, drawdown=0, valid=true
  for(const [day,equity] of [...endOfDay].sort(([a],[b])=>a.localeCompare(b))){
    const key=day.slice(0,6), m=months.get(key) || {key,base:previous}
    m.end=equity;months.set(key,m)
    if(previous>0) returns.push(equity/previous-1);else valid=false
    previous=equity;peak=Math.max(peak,equity);drawdown=Math.max(drawdown,1-equity/peak)
  }
  const n=returns.length, mean=n?returns.reduce((a,b)=>a+b,0)/n:0
  const volatility=valid && n>1?Math.sqrt(returns.reduce((s,x)=>s+(x-mean)**2,0)/(n-1)*sessions):null
  const annual=valid && n>1 && previous>0?(previous/capital)**(sessions/n)-1:null
  return {months:[...months.values()].map(m=>({key:m.key,value:m.base>0?m.end/m.base-1:null})),
    stats:{days:endOfDay.size,annual:Number.isFinite(annual)?annual:null,volatility,drawdown:n?drawdown:null,
           sharpe:volatility>0?mean*sessions/volatility:null,calmar:annual!=null && drawdown>0?annual/drawdown:null}}
}
