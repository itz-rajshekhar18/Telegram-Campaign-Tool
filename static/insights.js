/* Dedicated conversion ledger and date-filtered analytics views. */
const insightState = {conversions:{campaign_id:'',currency:'',status:'',search:'',offset:0},analytics:{campaign_id:'',currency:''},ledger:null,analyticsData:null};
let conversionLoadVersion=0;

function insightDates(filters){
  if(!filters.end){filters.end=localDate();const start=new Date(filters.end+'T12:00:00Z');start.setUTCDate(start.getUTCDate()-state.workspace.settings.report_days+1);filters.start=start.toISOString().slice(0,10);}
  return filters;
}
function money(value,currency=state.workspace.settings.default_currency){
  try{return new Intl.NumberFormat(undefined,{style:'currency',currency,maximumFractionDigits:2}).format(value||0);}catch{return `${currency} ${Number(value||0).toFixed(2)}`;}
}
function insightQuery(filters){const params=new URLSearchParams();for(const [key,value] of Object.entries(filters))if(value!==''&&value!==undefined&&value!==null)params.set(key,String(value));return params;}
function insightFilters(page){
  const f=insightDates(insightState[page]);
  const currencies=[...new Set([...state.workspace.currencies,...state.campaigns.map(c=>c.currency).filter(Boolean),...(insightState.analyticsData?.currencies||[]),...Object.keys(insightState.ledger?.summary||{})])];
  return `<form id="insight-filters" class="insight-filters" data-insight-page="${page}"><label>Campaign<select name="campaign_id"><option value="">All campaigns</option>${state.campaigns.map(c=>`<option value="${c.id}" ${String(c.id)===String(f.campaign_id)?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label><label>From<input name="start" type="date" required value="${esc(f.start)}"></label><label>To<input name="end" type="date" required value="${esc(f.end)}"></label><label>Currency<input name="currency" list="known-currencies" pattern="[A-Z]{3}" maxlength="3" value="${esc(f.currency)}" ${page==='conversions'?'placeholder="All currencies"':'required'}><datalist id="known-currencies">${currencies.map(c=>`<option value="${esc(c)}"></option>`).join('')}</datalist></label>${page==='conversions'?`<label>Status<select name="status"><option value="">All statuses</option>${state.workspace.options.conversion_statuses.map(s=>`<option ${s===f.status?'selected':''}>${s}</option>`).join('')}</select></label><label class="filter-search">Search<input type="search" name="search" maxlength="160" placeholder="Name or order reference" value="${esc(f.search)}"></label>`:''}<button class="primary" type="submit">Apply filters</button><div class="range-shortcuts">${[7,30,90].map(days=>`<button type="button" class="small-button" data-insight-action="range" data-days="${days}">Last ${days} days</button>`).join('')}</div><div class="form-error" role="alert"></div></form>`;
}
function insightCard(label,value,detail,extra=''){return `<div class="stat ${extra}"><div class="stat-label">${esc(label)}</div><div class="stat-value">${value}</div><small>${esc(detail)}</small></div>`;}

async function conversionsView(){
  const filters=insightDates(insightState.conversions);
  const d=await api('/api/studio/conversions?'+insightQuery(filters));insightState.ledger=d;
  const groups=Object.entries(d.summary);
  const count=key=>groups.reduce((n,[,s])=>n+(s[key]||0),0);
  const revenue=groups.length?groups.map(([code,s])=>`<span class="money-line">${esc(money(s.revenue,code))}</span>`).join(''):'0';
  return `${insightFilters('conversions')}<div class="stats insight-stats">${insightCard('Recorded outcomes',d.total.toLocaleString(),'All matching ledger records')}${insightCard('Confirmed',count('confirmed').toLocaleString(),'Confirmed individual outcomes')}${insightCard('Confirmed value',revenue,'Currencies are kept separate','money-stat')}${insightCard('Pending / refunded',`${count('pending')} <small>/ ${count('refunded')}</small>`,'Cancelled: '+count('cancelled'))}</div><div class="toolbar"><div><h2>Conversion ledger</h2><p class="helper">Individual records you enter. Saved records appear automatically in Reports, separately from entered aggregate totals.</p></div><div class="actions"><button class="secondary" data-insight-action="export-conversions">Export CSV</button><button class="primary" data-insight-action="new-conversion" ${state.campaigns.length?'':'disabled'}>+ Record conversion</button></div></div><div class="panel">${d.items.length?`<div class="table-wrap"><table><thead><tr><th>Outcome</th><th>Campaign</th><th>Status</th><th>Value</th><th>Source</th><th>Date</th><th></th></tr></thead><tbody>${d.items.map(row=>`<tr><td class="wrap"><strong>${esc(row.name)}</strong><span class="subline">${esc(row.kind)}${row.reference?' · '+esc(row.reference):''}</span></td><td class="wrap">${esc(row.campaign_name)}</td><td>${badge(row.status)}</td><td>${esc(money(row.amount,row.currency))}</td><td>${esc(row.source)}</td><td>${date(row.occurred_on)}</td><td><button class="small-button" data-insight-action="edit-conversion" data-id="${row.id}">Edit</button></td></tr>`).join('')}</tbody></table></div><div class="pagination"><span class="muted">${d.offset+1}–${Math.min(d.offset+d.items.length,d.total)} of ${d.total}</span><div class="actions"><button class="small-button" data-insight-action="previous" ${d.offset===0?'disabled':''}>← Previous</button><button class="small-button" data-insight-action="next" ${d.offset+d.limit>=d.total?'disabled':''}>Next →</button></div></div>`:empty('No conversions in this range',state.campaigns.length?'Record an outcome or adjust the campaign, date, and status filters.':'Create a campaign first, then record its outcomes.')}</div><p class="helper">Order references prevent duplicate records within a campaign. Update a record to refunded or cancelled to keep its history. Confirmed value excludes pending, refunded, and cancelled records.</p>`;
}

function trendChart(rows){
  const width=760,height=235,left=42,right=18,top=22,bottom=36;
  const series=[['sent','Live sends','#137c72'],['clicks','Entered clicks','#7c8ed0'],['recorded_conversions','Ledger conversions','#d1a05c']];
  const maximum=Math.max(1,...rows.flatMap(r=>series.map(([key])=>r[key])));
  const ceiling=Math.max(1,Math.ceil(maximum/4)*4);
  const x=i=>left+i*(width-left-right)/Math.max(1,rows.length-1);
  const y=v=>height-bottom-(v/ceiling)*(height-top-bottom);
  const ticks=Array.from({length:5},(_,i)=>ceiling*i/4);
  const grid=ticks.map(v=>`<line x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}" stroke="#e8eef0"/><text x="${left-10}" y="${y(v)+4}" text-anchor="end" fill="#8a9aa2" font-size="10">${Number.isInteger(v)?v:v.toFixed(1)}</text>`).join('');
  const labels=rows.map((r,i)=>i===0||i===rows.length-1||i%Math.max(1,Math.ceil(rows.length/6))===0?`<text x="${x(i)}" y="${height-10}" text-anchor="middle" fill="#8a9aa2" font-size="10">${esc(r.day.slice(5))}</text>`:'').join('');
  const lines=series.map(([key,label,color])=>`<polyline points="${rows.map((r,i)=>`${x(i)},${y(r[key])}`).join(' ')}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>${rows.map((r,i)=>`<circle tabindex="0" cx="${x(i)}" cy="${y(r[key])}" r="${rows.length>90?2:3}" fill="${color}" class="trend-point"><title>${esc(r.day)} · ${label}: ${r[key]}</title></circle>`).join('')}`).join('');
  return `<div class="chart-legend">${series.map(([,label,color])=>`<span><i style="background:${color}"></i>${label}</span>`).join('')}</div><div class="chart-wrap"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Daily live sends, manually entered clicks, and confirmed ledger conversions"><title>Daily campaign activity</title><desc>Delivery dates use UTC. Clicks and conversions use their saved reporting dates. Values are independent series.</desc>${grid}${lines}${labels}</svg></div>`;
}
function funnelChart(d){
  const steps=[['Delivered',d.manual.delivered],['Clicks',d.manual.clicks],['Conversions',d.manual.conversions]];
  const maximum=Math.max(1,...steps.map(([,v])=>v));
  return steps.map(([label,value],i)=>`<div class="funnel-row"><div><span>${label}</span><strong>${value.toLocaleString()}</strong></div><div class="funnel-track"><span style="width:${value/maximum*100}%;opacity:${1-i*.2}"></span></div><small>${i===0?'Entered delivered count':steps[i-1][1]?`${(value/steps[i-1][1]*100).toFixed(1)}% of previous step`:'No previous-step data'}</small></div>`).join('');
}
async function analyticsView(){
  const d=await api('/api/studio/analytics?'+insightQuery(insightDates(insightState.analytics)));insightState.analyticsData=d;
  return `${insightFilters('analytics')}<div class="stats insight-stats">${insightCard('Messages sent',d.live.sent.toLocaleString(),'Confirmed Telegram sends in range')}${insightCard('Recorded conversions',d.ledger.confirmed.toLocaleString(),'Confirmed individual ledger records')}${insightCard('Confirmed value',esc(money(d.ledger.revenue,d.currency)),`${d.currency} only · excludes refunded records`,'money-stat')}${insightCard('Return on ad spend',d.finance.roas===null?'—':`${d.finance.roas.toFixed(2)}×`,'Ledger value ÷ entered spend')}</div><div class="panel"><div class="panel-heading"><div><h3>Activity over time</h3><p class="helper">Daily counts from the delivery queue, entered results, and conversion ledger.</p></div><button class="small-button" data-insight-action="export-trend">Export trend</button></div>${trendChart(d.trend)}${d.trend.every(r=>!r.sent&&!r.clicks&&!r.recorded_conversions)?'<p class="chart-empty">Your activity will appear here as you send messages and record results.</p>':''}</div><div class="grid2"><div class="panel"><h3>Reported conversion funnel</h3><p class="helper">Uses daily aggregate metrics only. Events are counts, not deduplicated people.</p>${funnelChart(d)}<p class="helper">Click rate: <strong>${d.rates.click_rate===null?'—':d.rates.click_rate+'%'}</strong> · Delivered-to-conversion rate: <strong>${d.rates.conversion_rate===null?'—':d.rates.conversion_rate+'%'}</strong>. Outcomes may overlap or occur outside a click path.</p></div><div class="panel"><h3>Budget & delivery health</h3><div class="finance-list"><div><span>Planned budget · ${esc(d.currency)}</span><strong>${esc(money(d.finance.budget,d.currency))}</strong></div><div><span>Entered spend · ${esc(d.currency)}</span><strong>${esc(money(d.finance.spend,d.currency))}</strong></div><div><span>Refunded value · ${esc(d.currency)}</span><strong>${esc(money(d.ledger.refund_value,d.currency))}</strong></div><div><span>Failed / uncertain sends</span><strong>${d.live.failed} / ${d.live.unknown}</strong></div><div><span>Live incoming replies</span><strong>${d.live.replies}</strong></div><div><span>Reported opt-outs / complaints</span><strong>${d.manual.opt_outs} / ${d.manual.complaints}</strong></div></div><p class="helper">Spend includes campaigns configured in ${esc(d.currency)}. Revenue includes ledger records in ${esc(d.currency)}. No exchange rates are applied. Budget shows the current plan.</p></div></div><div class="panel"><div class="panel-heading"><h3>Campaign comparison</h3><button class="small-button" data-insight-action="export-analytics">Export comparison</button></div>${d.campaigns.length?`<div class="table-wrap"><table><thead><tr><th>Campaign</th><th>Live sends</th><th>Entered clicks</th><th>Entered conversions</th><th>Ledger conversions</th><th>Spend · ${esc(d.currency)}</th><th>Value · ${esc(d.currency)}</th></tr></thead><tbody>${d.campaigns.map(c=>`<tr><td class="wrap">${esc(c.name)}</td><td>${c.sent}</td><td>${c.clicks}</td><td>${c.manual_conversions}</td><td>${c.recorded_conversions}</td><td>${esc(money(c.spend,d.currency))}</td><td>${esc(money(c.revenue,d.currency))}</td></tr>`).join('')}</tbody></table></div>`:empty('No campaigns to compare','Create a campaign to begin tracking its performance.')}</div><div class="grid2"><div class="panel"><h3>Conversion sources</h3><p class="helper">Confirmed ledger events across currencies; value is ${esc(d.currency)} only.</p>${d.sources.length?d.sources.map(s=>`<div class="source-row"><strong>${esc(s.source)}</strong><span>${s.count} outcomes</span><span>${esc(money(s.revenue,d.currency))}</span></div>`).join(''):empty('No source data yet','Record a conversion and choose its source.')}</div><div class="panel"><h3>Current audience pipeline</h3><p class="helper">Current contact stages for the selected campaigns, independent of the date range.</p>${state.workspace.options.stages.map(s=>`<div class="pipeline-row"><span>${s}</span><div class="progress"><span style="width:${(d.stages[s]||0)/Math.max(1,...Object.values(d.stages))*100}%"></span></div><strong>${d.stages[s]||0}</strong></div>`).join('')}</div></div><p class="helper">Ledger records and entered aggregate conversions remain independent. Live delivery and reply dates use UTC. Entered results and ledger events use their saved dates. An empty view shows zeroes rather than sample data.</p>`;
}

function appendCampaignDetails(c){
  const panel=document.createElement('div');panel.className='panel';
  panel.innerHTML=`<div class="panel-heading"><h3>Campaign details</h3><button class="small-button" data-action="edit">Edit details</button></div><div class="facts">${field('Category',c.category?.replaceAll('_',' '))}${field('Planned budget',money(c.budget,c.currency))}${field('Language & tone',`${c.language} · ${c.tone}`)}${field('Tags',c.tags)}${field('Landing page',c.landing_page)}${field('Internal notes',c.internal_notes)}</div>`;
  $('page-content').append(panel);
}
async function loadConversionOptions(campaignId,selectedContact='',selectedVariant=''){
  const version=++conversionLoadVersion;
  const form=$('conversion-form');const button=form.querySelector('[type="submit"]');button.disabled=true;
  try{
    const [campaign,audience]=await Promise.all([api(`/api/campaigns/${campaignId}`),api(`/api/studio/campaigns/${campaignId}/audience`)]);
    if(version!==conversionLoadVersion)return;
    $('conversion-contact').innerHTML='<option value="">Unattributed</option>'+audience.subscribers.map(s=>`<option value="${s.id}">${esc(s.name||s.chat_id)}</option>`).join('');
    $('conversion-variant').innerHTML='<option value="">Unattributed</option>'+campaign.variants.map(v=>`<option value="${v.id}">${esc(v.label)}</option>`).join('');
    $('conversion-contact').value=selectedContact||'';$('conversion-variant').value=selectedVariant||'';
    return campaign;
  }finally{if(version===conversionLoadVersion)button.disabled=false;}
}
async function openConversion(id=null){
  const form=$('conversion-form');form.reset();form.querySelector('.form-error').textContent='';
  const row=id?await api(`/api/studio/conversions/${id}`):null;
  $('conversion-editor-title').textContent=id?'Edit conversion':'Record a conversion';
  $('conversion-campaign').innerHTML=state.campaigns.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const campaignId=row?.campaign_id||Number(insightState.conversions.campaign_id)||state.selected||state.campaigns[0]?.id;
  if(!campaignId)throw new Error('Create a campaign first.');
  form.elements.campaign_id.value=campaignId;form.elements.occurred_on.value=localDate();
  $('conversion-editor').showModal();
  try{
    const c=await loadConversionOptions(campaignId,row?.subscriber_id,row?.variant_id);
    if(row){for(const el of form.elements)if(el.name&&row[el.name]!==undefined&&row[el.name]!==null){if(el.name==='currency'&&el.tagName==='SELECT'&&![...el.options].some(o=>o.value===row.currency))el.add(new Option(row.currency,row.currency));el.value=row[el.name];}}
    else form.elements.currency.value=c.currency;
  }catch(e){form.querySelector('.form-error').textContent=e.message;form.querySelector('[type="submit"]').disabled=true;}
}
function readInsightFilters(){const form=$('insight-filters');const page=form.dataset.insightPage;const values=Object.fromEntries(new FormData(form));Object.assign(insightState[page],values);if(page==='conversions')insightState.conversions.offset=0;return page;}
function downloadBlob(blob,filename){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}

document.addEventListener('click',async event=>{
  const b=event.target.closest('[data-insight-action]');if(!b)return;
  b.disabled=true;
  try{
    const action=b.dataset.insightAction;
    if(action==='new-conversion')await openConversion();
    if(action==='edit-conversion')await openConversion(Number(b.dataset.id));
    if(action==='range'){const page=readInsightFilters();const f=insightState[page];f.end=localDate();const start=new Date(f.end+'T12:00:00Z');start.setUTCDate(start.getUTCDate()-Number(b.dataset.days)+1);f.start=start.toISOString().slice(0,10);await render();}
    if(action==='next'||action==='previous'){const d=insightState.ledger;insightState.conversions.offset=Math.max(0,d.offset+(action==='next'?d.limit:-d.limit));await render();}
    if(action==='export-conversions'){const response=await fetch('/api/studio/conversion-export?'+insightQuery({...insightState.conversions,offset:null}),{headers:{'X-Telegram-Init-Data':tg?.initData||''}});if(!response.ok)throw new Error('Could not export conversions.');downloadBlob(await response.blob(),'conversions.csv');}
    if(action==='export-trend')await downloadAnalyticsCSV('daily');
    if(action==='export-analytics')await downloadAnalyticsCSV('campaigns');
  }catch(e){toast(e.message,true);}finally{if(b.isConnected)b.disabled=false;}
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(form.id!=='conversion-form'&&form.id!=='insight-filters')return;
  event.preventDefault();
  const error=form.querySelector('.form-error');error.textContent='';const button=form.querySelector('[type="submit"]');button.disabled=true;
  try{
    if(form.id==='insight-filters'){readInsightFilters();await render();return;}
    const values=Object.fromEntries(new FormData(form));const id=values.id;delete values.id;
    values.campaign_id=Number(values.campaign_id);values.amount=Number(values.amount);
    values.subscriber_id=values.subscriber_id?Number(values.subscriber_id):null;values.variant_id=values.variant_id?Number(values.variant_id):null;values.reference=values.reference||null;
    await api(id?`/api/studio/conversions/${id}`:'/api/studio/conversions',{method:id?'PUT':'POST',body:JSON.stringify(values)});
    $('conversion-editor').close();toast(id?'Conversion updated':'Conversion recorded');await render();
  }catch(e){error.textContent=e.message;}finally{if(button.isConnected)button.disabled=false;}
});
document.addEventListener('change',async event=>{
  if(event.target.id!=='conversion-campaign')return;
  const form=$('conversion-form');const error=form.querySelector('.form-error');error.textContent='';
  try{const c=await loadConversionOptions(Number(event.target.value));if(c)form.elements.currency.value=c.currency;}catch(e){error.textContent=e.message;form.querySelector('[type="submit"]').disabled=true;}
});
