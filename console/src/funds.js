// Same financial semantics as the shipped legacy desktop and mobile bundles.
export function portfolioFunds(rows) {
  let previous = null, totalfee = 0, totalprofit = 0
  return [...rows].sort((a, b) => Number(a.date) - Number(b.date)).map(source => {
    const row = { ...source, dynbalance: Number(source.balance) + Number(source.dynprofit) }
    row.profit = Number(row.closeprofit) + Number(row.dynprofit) - Number(previous?.dynprofit || 0) - Number(row.fee)
    row.totalfee = totalfee += Number(row.fee || 0)
    row.totalprofit = totalprofit += Number(row.closeprofit || 0)
    previous = row
    return row
  })
}
// Strategy funds.csv contains cumulative closeprofit/fee/dynbalance.
export function strategyFunds(rows, aggregated = false) {
  if (aggregated) return rows.map(row => ({ ...row }))
  let previous = null
  return [...rows].sort((a, b) => Number(a.date) - Number(b.date)).map(source => {
    const row = { ...source,
      profit: Number(source.closeprofit) - Number(previous?.closeprofit || 0),
      thisfee: Number(source.fee) - Number(previous?.fee || 0),
      addition: Number(source.dynbalance) - Number(previous?.dynbalance || 0)
    }
    previous = row
    return row
  })
}
