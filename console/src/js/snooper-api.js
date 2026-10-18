export async function request(path, data={}) {
  let result
  try {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    result=await response.json()
    if(!response.ok) throw new Error(result.message || result.detail?.[0]?.msg || response.statusText)
  } catch(error) { throw new Error(error.message || '网络请求失败') }
  if(result.result<0) throw new Error(result.message)
  return result
}
