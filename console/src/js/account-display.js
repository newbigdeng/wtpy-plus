// Historical display is separate from risk.valid and never authorizes trading.
export function displayAccount(risk,snapshot){
 const summary=risk?.broker_summary;
 if(summary?.valid){
  const base=risk.baseline?.value,valid=Boolean(risk.metrics?.day_loss_pct?.valid&&base>0);
  const pnl=valid?summary.equity-base:null;
  return {...summary,historical:false,margin_usage_pct:risk.metrics?.margin_usage_pct?.valid?risk.metrics.margin_usage_pct.value:null,
   daily:{valid,pnl,pnl_pct:valid?pnl/base*100:null,trading_date:risk.baseline?.tradingday}};
 }
 const rows=(snapshot?.account_snapshots||[]).filter(row=>!summary?.channel||row.channel===summary.channel&&(!summary.currency||row.currency===summary.currency));
 if(rows.length!==1)return null;
 const row=rows[0];
 return {...row,historical:true,margin_usage_pct:row.equity>0&&row.margin!=null?row.margin/row.equity*100:null};
}
