import {chartData} from './factorCharts.js'
let csv
self.onmessage=async({data:{id,window,sequence}})=>{
  try{
    if(csv===undefined){
      const r=await fetch('/mgr/research/factor_results/'+encodeURIComponent(id),{credentials:'same-origin'})
      if(!r.ok)throw Error('无法读取完整因子流水')
      csv=await r.text()
    }
    self.postMessage({sequence,data:chartData(csv,window)})
  }catch(e){self.postMessage({sequence,error:e.message})}
}
