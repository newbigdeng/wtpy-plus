// All windows end at the current bar; unfinished history remains null.
export function movingAverage(values, period) {
  let total = 0
  return values.map((value, index) => {
    total += value
    if (index >= period) total -= values[index - period]
    return index < period - 1 ? null : total / period
  })
}

// Seed each EMA with its first complete window's arithmetic mean.
function exponentialAverage(values, period) {
  let count = 0, total = 0, previous = null
  const alpha = 2 / (period + 1)
  return values.map(value => {
    if (value === null) return null
    if (count < period) {
      total += value
      count++
      if (count < period) return null
      previous = total / period
    } else previous += alpha * (value - previous)
    return previous
  })
}

export function calculateMacd(values, fast = 12, slow = 26, signal = 9) {
  const fastEma = exponentialAverage(values, fast)
  const slowEma = exponentialAverage(values, slow)
  const dif = values.map((_, index) => slowEma[index] === null ? null : fastEma[index] - slowEma[index])
  const dea = exponentialAverage(dif, signal)
  return {dif, dea, histogram: dif.map((value, index) => dea[index] === null ? null : 2 * (value - dea[index]))}
}

export function calculateKdj(bars, period = 9, kPeriod = 3, dPeriod = 3) {
  const k = [], d = [], j = []
  let previousK = 50, previousD = 50
  for (let index = 0; index < bars.length; index++) {
    if (index < period - 1) {
      k.push(null); d.push(null); j.push(null)
      continue
    }
    let high = -Infinity, low = Infinity
    for (let cursor = index - period + 1; cursor <= index; cursor++) {
      high = Math.max(high, bars[cursor].high)
      low = Math.min(low, bars[cursor].low)
    }
    // A flat window has no direction: keep RSV at its neutral value.
    const rsv = high === low ? 50 : (bars[index].close - low) / (high - low) * 100
    previousK += (rsv - previousK) / kPeriod
    previousD += (previousK - previousD) / dPeriod
    k.push(previousK); d.push(previousD); j.push(3 * previousK - 2 * previousD)
  }
  return {k, d, j}
}
