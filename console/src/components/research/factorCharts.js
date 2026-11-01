function mean(xs){return xs.reduce((sum,x)=>sum+x,0)/xs.length}
function ranks(xs){
  const order=xs.map((v,i)=>[v,i]).sort((a,b)=>a[0]-b[0]), result=[]
  for(let i=0;i<order.length;){
    let j=i+1
    while(j<order.length && order[i][0]===order[j][0])j++
    for(let k=i;k<j;k++)result[order[k][1]]=(i+j-1)/2
    i=j
  }
  return result
}
function corr(xs,ys){
  if(xs.length<3)return null
  const x=mean(xs),y=mean(ys)
  let xx=0,yy=0,xy=0
  for(let i=0;i<xs.length;i++){
    const a=xs[i]-x,b=ys[i]-y
    xx+=a*a;yy+=b*b;xy+=a*b
  }
  return xx && yy ? Math.max(-1,Math.min(1,xy/Math.sqrt(xx*yy))) : null
}
// The exported factor ledger contains numeric fields only.
export function chartData(csv,window){
  const [header,...lines]=csv.trim().replace(/^\uFEFF/,'').split(/\r?\n/)
  const fields=['value','forward_return','evaluation_entry','evaluation_exit'].map(k=>header.split(',').indexOf(k))
  if(fields.includes(-1))throw Error('逐点文件缺少因子值或评价区间')
  const blocks=[],nav=[],bucket=[]
  let capital=1,benchmark=1,lastExit='',pairs=0
  for(const line of lines){
    if(!line)continue
    const cells=line.split(','),[factor,target,entry,exit]=fields.map(i=>cells[i])
    if(!target || !entry || !exit || !factor)continue
    const x=Number(factor),y=Number(target)
    if(!Number.isFinite(x)||!Number.isFinite(y))throw Error('逐点文件包含无效数值')
    pairs++;bucket.push([x,y])
    if(bucket.length===window){
      const xs=bucket.map(r=>r[0]),ys=bucket.map(r=>r[1])
      blocks.push({time:exit,ic:corr(xs,ys),rank:corr(ranks(xs),ranks(ys))})
      bucket.length=0
    }
    if(entry>=lastExit){
      if(!nav.length)nav.push({time:entry,signal:1,benchmark:1})
      capital=Math.max(0,capital*(1+Math.sign(x)*y))
      benchmark*=1+y
      nav.push({time:exit,signal:capital,benchmark})
      lastExit=exit
    }
  }
  const stats={}
  for(const key of ['ic','rank']){
    const xs=blocks.map(b=>b[key]).filter(v=>v!==null)
    const avg=xs.length?mean(xs):null
    const sd=xs.length>1?Math.sqrt(xs.reduce((sum,x)=>sum+(x-avg)**2,0)/(xs.length-1)):null
    const histogram=Array(10).fill(0)
    for(const x of xs)histogram[Math.min(9,Math.floor((x+1)*5))]++
    stats[key]={n:xs.length,avg,sd,ir:sd>0?avg/sd:null,positive:xs.length?xs.filter(x=>x>0).length/xs.length:null,histogram}
  }
  return {pairs,blocks,stats,nav,remainder:bucket.length}
}
