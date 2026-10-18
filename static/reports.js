/* Reports are generated from the saved analytics/ledger sources and auto-saved server side. */
insightState.reports={campaign_id:'',currency:''};
let reportRequest=0;
function reportQuery(){return insightQuery(insightDates(insightState.reports)).toString();}
async function automaticReportsView(){
  const query=reportQuery();
  const data=await api('/api/studio/reports?'+query);
  return `${insightFilters('reports')}<div class="toolbar"><div><h2>Automatic report</h2><p class="helper">Uses saved Analytics and Conversions data. A report snapshot is saved automatically when opened, refreshed, or exported. Refreshes every 30 seconds while this page is open.</p></div><button class="secondary" data-report-refresh="true">Refresh report</button></div><div class="actions">${[['summary','Summary CSV'],['daily','Daily analytics CSV'],['campaigns','Campaign comparison CSV'],['conversions','Conversions CSV']].map(([kind,label])=>`<button class="small-button" data-report-export="${kind}">${label}</button>`).join('')}</div><p class="form-error" id="report-error" role="alert"></p><div id="report-output" data-query="${esc(query)}">${automaticReportBody(data)}</div>`;
}
function automaticReportBody(report){
  const d=report.analytics, ledger=report.conversions;
  const groups=Object.entries(ledger.summary);
  const counts=key=>groups.reduce((n,[,s])=>n+(s[key]||0),0);
  return `<p class="helper">Auto-saved report #${report.id} · Last data change saved ${esc(new Date(report.saved_at).toLocaleString())} · ${esc(d.start)} to ${esc(d.end)}</p><div class="stats insight-stats">${insightCard('Messages sent',d.live.sent.toLocaleString(),'Confirmed Telegram deliveries')}${insightCard('Confirmed conversions',d.ledger.confirmed.toLocaleString(),'Individual conversion records')}${insightCard('Confirmed value',esc(money(d.ledger.revenue,d.currency)),d.currency+' ledger value only')}${insightCard('Spend',esc(money(d.finance.spend,d.currency)),'Saved daily metrics in '+d.currency)}</div><div class="grid2"><div class="panel"><h3>Analytics summary</h3><div class="facts">${field('Reported delivered',d.manual.delivered)}${field('Reported clicks',d.manual.clicks)}${field('Reported aggregate conversions',d.manual.conversions)}${field('Live replies',d.live.replies)}${field('Failed / uncertain sends',`${d.live.failed} / ${d.live.unknown}`)}${field('ROAS',d.finance.roas===null?'No recorded spend':d.finance.roas+'×')}</div><p class="helper">Reported aggregate conversions and ledger conversions are separate measurements and are never added together.</p></div><div class="panel"><h3>Conversions by currency</h3>${groups.length?`<div class="table-wrap"><table><thead><tr><th>Currency</th><th>Confirmed</th><th>Pending</th><th>Refunded / cancelled</th><th>Confirmed value</th></tr></thead><tbody>${groups.map(([code,s])=>`<tr><td>${esc(code)}</td><td>${s.confirmed}</td><td>${s.pending}</td><td>${s.refunded} / ${s.cancelled}</td><td>${esc(money(s.revenue,code))}</td></tr>`).join('')}</tbody></table></div>`:empty('No conversion records','Saved conversion records in this range appear here automatically.')}<p class="helper">${ledger.total} records · ${counts('pending')} pending · ${counts('refunded')} refunded · ${counts('cancelled')} cancelled. Amounts are never combined across currencies.</p></div></div><div class="panel"><h3>Daily activity</h3>${trendChart(d.trend)}</div><div class="panel"><h3>Campaign comparison</h3><p class="helper">Financial totals below use ${esc(d.currency)}. Counts include all selected campaigns.</p><div class="table-wrap"><table><thead><tr><th>Campaign</th><th>Live sends</th><th>Reported clicks</th><th>Reported conversions</th><th>Ledger conversions</th><th>Spend</th><th>Value</th></tr></thead><tbody>${d.campaigns.map(c=>`<tr><td>${esc(c.name)}</td><td>${c.sent}</td><td>${c.clicks}</td><td>${c.manual_conversions}</td><td>${c.recorded_conversions}</td><td>${esc(money(c.spend,d.currency))}</td><td>${esc(money(c.revenue,d.currency))}</td></tr>`).join('')||'<tr><td colspan="7">No campaigns found.</td></tr>'}</tbody></table></div></div><div class="panel"><h3>Recent conversion records</h3><p class="helper">Latest 10 of ${ledger.total} matching records. Conversions CSV exports every matching record across currencies.</p>${ledger.items.length?`<div class="table-wrap"><table><thead><tr><th>Outcome</th><th>Campaign</th><th>Status</th><th>Value</th><th>Source</th><th>Date</th></tr></thead><tbody>${ledger.items.map(r=>`<tr><td>${esc(r.name)}</td><td>${esc(r.campaign_name)}</td><td>${badge(r.status)}</td><td>${esc(money(r.amount,r.currency))}</td><td>${esc(r.source)}</td><td>${esc(r.occurred_on)}</td></tr>`).join('')}</tbody></table></div>`:empty('No records in this range','Record conversions or change the report filters.')}</div>`;
}
async function refreshAutomaticReport(){
  if(state.page!=='reports')return;
  const target=$('report-output');if(!target)return;
  const version=++reportRequest, query=target.dataset.query;
  try{
    const data=await api('/api/studio/reports?'+query);
    if(version===reportRequest&&target.isConnected&&target.dataset.query===query){target.innerHTML=automaticReportBody(data);$('report-error').textContent='';}
  }catch(e){if(version===reportRequest&&target.isConnected)$('report-error').textContent='Auto-refresh failed. Showing the previous report. '+e.message;}
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-report-export],[data-report-refresh]');if(!button)return;
  button.disabled=true;
  try{
    if(button.dataset.reportRefresh){await refreshAutomaticReport();return;}
    // Export the applied filters shown in the report, not unsubmitted form changes.
    const query=$('report-output').dataset.query;
    const kind=button.dataset.reportExport;
    const response=await fetch('/api/studio/report-export?'+query+'&kind='+kind,{headers:{'X-Telegram-Init-Data':tg?.initData||''}});
    if(!response.ok)throw new Error('Could not export this report. Refresh and try again.');
    downloadBlob(await response.blob(),`report-${kind}.csv`);
  }catch(e){toast(e.message,true);}finally{if(button.isConnected)button.disabled=false;}
});
let reportRefreshTimer;
function configureReportRefresh(seconds){
  clearInterval(reportRefreshTimer);
  reportRefreshTimer=setInterval(()=>{if(!document.hidden&&state.page==='reports'&&!document.querySelector('dialog[open]'))refreshAutomaticReport();},seconds*1000);
}
