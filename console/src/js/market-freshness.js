export function marketFreshness(market, now=Date.now()) {
 const quote=market?.quote, date=String(quote?.action_date||''),time=String(quote?.action_time||'').padStart(9,'0')
 const stamp=date.length===8?Date.parse(`${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6)}T${time.slice(0,2)}:${time.slice(2,4)}:${time.slice(4,6)}+08:00`):NaN
 const age=Number.isFinite(stamp)?Math.max(0,Math.floor((now-stamp)/1000)):null
 if(Number.isFinite(stamp)&&stamp>now+5000)return {state:'unknown',label:'时间异常 · 行情时间领先',age:null}
 if(market?.history_mode)return {state:'history',label:'HISTORY · 历史查询',age}
 if(market?.session_open===false || market?.session_state==='closed')return {state:'closed',label:'CLOSED · 休市',age}
 if(market?.session_open!==true)return {state:'unknown',label:'时段未知 · '+(age==null?'暂无 Tick':age>60?'行情已过期':'行情快照'),age}
 if(age==null)return {state:'stale',label:'STALE · 等待行情',age}
 if(age<=5)return {state:'live',label:'LIVE · '+age+' 秒前',age}
 if(age<=60)return {state:'delayed',label:'DELAYED · 延迟 '+age+' 秒',age}
 return {state:'stale',label:'STALE · '+(age<3600?Math.floor(age/60)+' 分钟前':Math.floor(age/3600)+' 小时前'),age}
}
