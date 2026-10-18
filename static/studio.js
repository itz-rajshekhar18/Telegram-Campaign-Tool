/* Campaign Studio: plain JavaScript, Telegram Mini App and local browser. */
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tg = window.Telegram?.WebApp;
const state = {page:'campaigns', campaigns:[], selected:null, detail:null, tab:'brief', filter:'all', search:'', connection:null, session:null, render:0};
const labels = {campaigns:['Your campaigns','Plan, send, and learn. All in one place.'],audience:['Your audience','Build relationships with people who want to hear from you.'],inbox:['Conversations','Keep the conversation going, one reply at a time.'],conversions:['Conversions','Keep a clear record of the outcomes your campaigns create.'],analytics:['Analytics','See what is working, where people engage, and what to improve.'],reports:['Campaign insights','Turn your campaign results into a better next message.'],settings:['Telegram connection','Your bot, your workspace, ready when you are.']};
let toastTimer, confirmCallback;
tg?.ready(); tg?.expand();
tg?.setHeaderColor?.('#f5f7f9'); tg?.setBackgroundColor?.('#f5f7f9');

async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg?.initData || '',...options.headers}});
  const data = await response.json().catch(()=>({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(e=>`${e.loc?.slice(1).join('.') || 'Form'}: ${e.msg}`).join('; ') : data.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data;
}
const post = (path, data={}) => api(path,{method:'POST',body:JSON.stringify(data)});
function toast(message, error=false) {
  clearTimeout(toastTimer); $('toast').textContent=message; $('toast').className=`toast${error?' error':''}`;
  toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),6500);
}
function badge(status){return `<span class="badge ${esc(status)}">${esc(status?.replaceAll('_',' ') || 'draft')}</span>`;}
function date(value, time=false){if(!value)return '—';const d=new Date(value.includes('T')&&!/[Z+]\d*/.test(value.slice(10))?value+'Z':value);return Number.isNaN(d.getTime())?value:d.toLocaleDateString(undefined,{month:'short',day:'numeric',...(time?{hour:'2-digit',minute:'2-digit'}:{})});}
function empty(title,description,action=''){return `<div class="empty"><strong>${esc(title)}</strong>${esc(description)}${action}</div>`;}
function metricInput(name,label,value=0){return `<label>${label}<input name="${name}" type="number" min="0" ${name==='spend'?'step="0.01"':'step="1"'} value="${esc(value)}" required></label>`;}
function field(label,value){return `<div><div class="fact-label">${label}</div><div class="fact-value">${esc(value===0?'0':value || 'Not provided')}</div></div>`;}
function getCampaign(){return state.campaigns.find(c=>c.id===state.selected);}

async function loadCampaigns(){
  state.campaigns=await api('/api/campaigns');
  if(!state.campaigns.some(c=>c.id===state.selected))state.selected=state.campaigns[0]?.id || null;
  $('campaign-select').innerHTML=state.campaigns.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  $('campaign-select').value=state.selected || '';
  $('campaign-context-status').innerHTML=badge(getCampaign()?.status);
}
async function loadStats(){
  const data=await api('/api/studio/overview');
  $('stats').innerHTML=[['Total campaigns',data.campaigns,'All campaigns in your workspace','▦'],['Active subscriptions',data.subscribers,'Opted-in campaign contacts','♧'],['Campaign messages sent',data.sent,'Confirmed by Telegram','↗'],['Replies received',data.replies,`${data.queued} sends in progress`,'☷']].map(([label,value,sub,icon])=>`<div class="stat"><div class="stat-label">${label}<span>${icon}</span></div><div class="stat-value">${Number(value).toLocaleString()}</div><small>${sub}</small></div>`).join('');
  $('global-alert').textContent=data.sync_error || '';
  $('global-alert').classList.toggle('hidden',!data.sync_error);
}

async function render(){
  const version=++state.render;
  const [title,desc]=labels[state.page];
  $('page-title').textContent=title;$('page-description').textContent=desc;
  $('page-crumb').textContent=state.page==='settings'?'Connection':state.page[0].toUpperCase()+state.page.slice(1);
  document.querySelectorAll('[data-page]').forEach(el=>{el.classList.toggle('active',el.dataset.page===state.page);el.setAttribute('aria-current',el.dataset.page===state.page?'page':'false');});
  $('stats').classList.toggle('hidden',['settings','conversions','analytics','reports'].includes(state.page));
  $('campaign-context').classList.toggle('hidden',state.page!=='audience');
  $('campaign-select').value=state.selected || '';
  $('campaign-context-status').innerHTML=badge(getCampaign()?.status);
  try {
    let html;
    if(state.page==='campaigns') html=state.detail?await campaignWorkspace():campaignGrid();
    if(state.page==='audience') html=state.selected?await audienceView():empty('Start with a campaign','Create a campaign to organize its subscribers.', '<button class="primary" data-action="new">Create campaign</button>');
    if(state.page==='inbox')html=await inboxView();
    if(state.page==='reports')html=await automaticReportsView();
    if(state.page==='settings')html=workspacePanel()+await connectionToolsView()+connectionView()+await directHistoryView();
    if(state.page==='conversions')html=await conversionsView();
    if(state.page==='analytics')html=await analyticsView();
    if(state.selected&&state.page==='campaigns'&&state.detail&&state.tab==='results')html=`<div class="toolbar"><span class="muted">Campaign reporting</span><div class="actions"><button class="small-button" data-action="refresh">Refresh insights</button><button class="small-button" data-action="export" data-export-kind="metrics">Export metrics</button><button class="small-button" data-action="export" data-export-kind="deliveries">Export deliveries</button></div></div>`+html;
    if(version===state.render){$('page-content').innerHTML=html; if(state.page==='campaigns'&&!state.detail)filterCards();if(state.page==='campaigns'&&state.detail&&state.tab==='brief')appendCampaignDetails(state.detail);}
  } catch(e){if(version===state.render)$('page-content').innerHTML=empty('Could not load this view',e.message,'<button class="secondary" data-action="refresh">Try again</button>');}
}

function campaignGrid(){
  return `<div class="toolbar"><h2>Campaign collection <span class="muted">(${state.campaigns.length})</span></h2><div class="filters">${['all',...state.workspace.options.campaign_statuses].map(f=>`<button class="filter ${state.filter===f?'active':''}" data-filter="${f}">${f[0].toUpperCase()+f.slice(1)}</button>`).join('')}<input aria-label="Search campaigns" id="campaign-search" class="search" placeholder="Search campaigns…" value="${esc(state.search)}"></div></div><div class="cards" id="campaign-cards">${state.campaigns.map(c=>`<article class="card campaign-card" data-campaign="${c.id}"><div class="card-top"><span class="campaign-icon">↗</span>${badge(c.status)}</div><h3>${esc(c.name)}</h3><p>${esc(c.objective)}</p><div class="card-meta"><span>◎ ${esc(c.target_value)} ${esc(c.primary_metric)}</span><span>◷ ${date(c.deadline)}</span></div><div class="card-bottom"><span>${esc(c.product_name || 'Campaign')}</span><button class="small-button" data-open="${c.id}">Open campaign ↗</button></div></article>`).join('')}<button class="card new-card" data-action="new"><span class="plus">+</span><h3>Create something great</h3><p>A clear goal, the right audience, and a message worth sending.</p></button></div><div id="no-matches" class="muted hidden">No campaigns match your search.</div>`;
}
function filterCards(){
  let count=0;
  document.querySelectorAll('.campaign-card').forEach(el=>{const c=state.campaigns.find(c=>c.id===Number(el.dataset.campaign));const visible=(state.filter==='all'||c.status===state.filter)&&`${c.name} ${c.objective} ${c.category||''} ${c.tags||''}`.toLowerCase().includes(state.search.toLowerCase());el.classList.toggle('hidden',!visible);if(visible)count++;});
  $('no-matches')?.classList.toggle('hidden',count>0||(!state.search&&state.filter==='all'));
}
async function campaignWorkspace(){
  const c=await api(`/api/campaigns/${state.selected}`);state.detail=c;
  let body='';
  if(state.tab==='brief')body=`<div class="grid2"><div class="panel"><div class="panel-heading"><h3>The campaign brief</h3><button class="small-button" data-action="edit">Edit brief</button></div><div class="facts">${field('Objective',c.objective)}${field('Audience',c.audience)}${field('Product',c.product_name)}${field('Value proposition',c.value_proposition)}${field('Offer',c.offer)}${field('Call to action',c.call_to_action)}${field('Goal',`${c.target_value} ${c.primary_metric} by ${date(c.deadline)}`)}${field('Estimated audience',c.audience_size)}</div><details><summary>Information bank</summary><div class="fact-value">${esc(c.info_bank)}</div></details></div><div class="panel"><div class="panel-heading"><h3>SMART goal check</h3><span class="score">${c.quality.smart.score}<small> / ${c.quality.smart.out_of}</small></span></div>${c.quality.smart.checks.map(k=>`<div class="quality-item"><span class="quality-check ${k.passed?'':'no'}">${k.passed?'✓':'○'}</span><div><strong>${esc(k.name)}</strong><small>${esc(k.detail)}</small></div></div>`).join('')}<p class="helper">These checks describe completeness. Review the actual goal and message before sending.</p><button class="primary" data-tab="drafts">Continue to message drafts →</button></div></div>`;
  if(state.tab==='drafts')body=draftsView(c);
  if(state.tab==='audience')body=await audienceView();
  if(state.tab==='delivery')body=await deliveryView(c);
  if(state.tab==='results')body=await reportsView(c);
  return `<button class="back" data-action="back">← All campaigns</button><div class="workspace-heading"><div><h2>${esc(c.name)}</h2><span class="muted">${esc(c.audience)}</span></div>${badge(c.status)}</div><div class="tabs">${[['brief','Brief & goals'],['drafts','Message drafts'],['audience','Audience'],['delivery','Delivery'],['results','Results']].map(([id,label])=>`<button class="tab ${state.tab===id?'active':''}" data-tab="${id}">${label}</button>`).join('')}</div>${body}`;
}
function draftsView(c){
  if(!c.variants.length)return empty('Give your brief a voice','Generate message drafts from your saved brief, then refine them before sending.','<button class="primary" data-action="generate">Generate drafts</button>');
  return `<div class="toolbar"><div><h3>Your saved message approaches</h3><p class="helper">Drafts are built from your brief using AIDA templates. Edit every section to make them yours.</p></div><button class="secondary" data-tab="delivery">Plan delivery →</button></div><div class="grid2">${c.variants.map(v=>`<form class="panel variant" data-variant="${v.id}"><div class="panel-heading"><h3>${esc(v.label)}</h3><span class="badge">${v.score}/${v.score_out_of} sections</span></div>${['attention','interest','desire','action'].map((key,i)=>`<label><span>0${i+1} · ${key}</span><textarea name="${key}" required maxlength="1000" rows="3">${esc(v[key])}</textarea></label>`).join('')}<details><summary>Telegram preview</summary><div class="message-preview" data-preview="${v.id}">${esc(v.copy)}</div></details><div class="actions"><button class="primary" type="submit">Save draft</button><button type="button" class="secondary" data-test-variant="${v.id}">Send test to me</button></div><div class="form-error" role="alert"></div></form>`).join('')}</div>`;
}
async function audienceView(){
  const d=await api(`/api/studio/campaigns/${state.selected}/audience`);
  const username=state.connection?.username;
  const link=username?`https://t.me/${username}?start=${d.join_code}`:'';
  return `<div class="panel"><div class="panel-heading"><div><h3>Invite people to subscribe</h3><p class="helper">Share this link with a clear invitation to receive this campaign’s updates. Pressing Start subscribes them; replying STOP opts them out.</p></div><button class="secondary" data-action="sync">↻ Sync Telegram</button></div>${link?`<div class="join-link"><input id="join-link" aria-label="Campaign subscribe link" readonly value="${esc(link)}"><button class="secondary" data-action="copy-link">Copy invite link</button></div>`:'<p class="notice">Connect your bot to generate a Telegram invite link.</p>'}</div><div class="panel"><div class="panel-heading"><h3>Contacts <span class="muted">${d.subscribers.filter(s=>s.active).length} subscribed · ${d.subscribers.length} total</span></h3><button class="small-button" data-action="export">Export CSV</button></div>${d.subscribers.length?`<div class="table-wrap"><table><thead><tr><th>Contact</th><th>Subscription</th><th>Stage</th><th>Consent</th><th></th></tr></thead><tbody>${d.subscribers.map(s=>`<tr><td>${esc(s.name || 'Telegram contact')}<span class="subline">${esc(s.username?'@'+s.username:s.chat_id)}</span></td><td>${badge(s.active?'subscribed':'unsubscribed')}</td><td><select aria-label="Lead stage for ${esc(s.name)}" data-stage="${s.id}" data-notes="${esc(s.notes)}">${state.workspace.options.stages.map(k=>`<option ${s.stage===k?'selected':''}>${k}</option>`).join('')}</select></td><td class="wrap">${esc(s.consent_source)}<span class="subline">${date(s.created_at)}</span></td><td><div class="actions"><button class="small-button" data-contact-notes="${s.id}" data-notes="${esc(s.notes)}" data-contact-stage="${esc(s.stage)}">Notes</button>${s.active?`<button class="small-button" data-unsubscribe="${s.id}">Unsubscribe</button>`:''}</div></td></tr>`).join('')}</tbody></table></div>`:empty('Your audience starts with a connection','Share your invite link or add someone who has already opted in.')}</div><div class="panel"><h3>Add an opted-in contact</h3><p class="helper">They must have started your bot in a private Telegram chat.</p><form id="contact-form"><div class="grid2"><label>Private chat ID<input name="chat_id" type="number" min="1" step="1" required placeholder="Numeric Telegram user ID"></label><label>Name <span class="optional">optional</span><input name="name" maxlength="120" placeholder="Their name"></label></div><label class="check"><input type="checkbox" name="consent_confirmed" required><span>This contact explicitly agreed to receive this campaign.</span></label><button class="primary">Add contact</button><div class="form-error" role="alert"></div></form></div>`;
}
async function deliveryView(c){
  const jobs=await api(`/api/studio/campaigns/${c.id}/deliveries`);
  const audience=await api(`/api/studio/campaigns/${c.id}/audience`);
  const count=audience.subscribers.filter(s=>s.active).length;
  const active=jobs.some(j=>['queued','running','paused'].includes(j.status));
  return `<div class="panel"><h3>Ready when you are</h3><p class="helper">${count} subscribed contacts · Messages are sent one at a time. Keep the server running for scheduled delivery.</p>${c.variants.length?`<form id="launch-form"><div class="grid2"><label>Saved message variant<select name="variant_id">${c.variants.map(v=>`<option value="${v.id}">${esc(v.label)}</option>`).join('')}</select></label><label>Schedule <span class="optional">optional · your local time</span><input type="datetime-local" name="scheduled_at"></label></div><button class="primary" ${!count||active?'disabled':''}>Review & launch →</button><div class="form-error" role="alert"></div></form>${!count?'<p class="helper">Add opted-in contacts in the Audience tab first.</p>':''}${active?'<p class="helper">There is an unfinished send. Complete or cancel it before launching another.</p>':''}`:'<button class="primary" data-tab="drafts">Create message drafts first</button>'}</div><div class="panel"><div class="panel-heading"><h3>Delivery history</h3><button class="small-button" data-action="refresh">↻ Refresh status</button></div><div id="delivery-history">${deliveryHistory(jobs)}</div></div>`;
}
function deliveryHistory(jobs){return jobs.length?jobs.map(j=>{const n=j.counts;const done=j.total-(n.pending||0)-(n.sending||0);return `<div class="job"><div class="job-top"><div><strong>Send #${j.id}</strong> ${badge(j.status)}<p class="helper">${date(j.scheduled_at,true)} · ${j.total} recipients</p></div><div class="actions">${['queued','running'].includes(j.status)?`<button class="small-button" data-job="${j.id}" data-job-action="pause">Pause</button>`:''}${j.status==='paused'?`<button class="small-button" data-job="${j.id}" data-job-action="resume">Resume</button>`:''}${['queued','running','paused'].includes(j.status)?`<button class="small-button" data-job="${j.id}" data-job-action="cancel">Cancel</button>`:''}</div></div><div class="progress"><span style="width:${j.total?done/j.total*100:0}%"></span></div><div class="job-counts">${['sent','pending','failed','unknown','skipped'].map(k=>`<span><strong>${n[k]||0}</strong> ${k}</span>`).join('')}</div><details><summary>View message & recipient results</summary><div class="message-preview">${esc(j.text)}</div><div class="table-wrap"><table><thead><tr><th>Contact</th><th>Status</th><th>Details</th></tr></thead><tbody>${j.deliveries.map(d=>`<tr><td>${esc(d.name||'Contact')}</td><td>${badge(d.status)}</td><td class="wrap">${esc(d.error || date(d.sent_at,true))}</td></tr>`).join('')}</tbody></table></div></details></div>`;}).join(''):empty('No sends yet','Your launches and recipient results will appear here.');}

async function reportsView(c){
  c=c||await api(`/api/campaigns/${state.selected}`);
  const [results,snapshots,jobs]=await Promise.all([api(`/api/campaigns/${c.id}/optimization`),api(`/api/campaigns/${c.id}/metrics`),api(`/api/studio/campaigns/${c.id}/deliveries`)]);
  state.snapshots=snapshots;state.reportVariants=c.variants;
  const actual=jobs.reduce((n,j)=>n+(j.counts.sent||0),0);
  const goal=results.goal;
  return `<div class="grid2"><div class="panel"><div class="panel-heading"><h3>Your goal</h3><span class="badge">${goal.progress_percent}%</span></div><div class="score">${goal.achieved.toLocaleString()}<small> / ${goal.target.toLocaleString()} ${esc(goal.metric)}</small></div><div class="progress"><span style="width:${goal.progress_percent}%"></span></div><p class="helper">By ${date(goal.deadline)} · Based on your manually recorded outcomes.</p></div><div class="panel"><h3>Telegram delivery</h3><div class="score">${actual}<small> messages sent</small></div><p class="helper">Live confirmed sends from delivery history. Read receipts, clicks, and conversions are not automatically tracked.</p></div></div><div class="panel"><h3>Variant performance</h3><p class="helper">The comparison below uses the daily figures you record. It is separate from the live delivery count.</p><div class="table-wrap"><table><thead><tr><th>Variant</th><th>Delivered</th><th>Clicks</th><th>${esc(c.primary_metric)}</th><th>Goal rate</th><th>Spend</th></tr></thead><tbody>${results.variants.map(r=>`<tr><td>${esc(r.label)}</td><td>${r.delivered}</td><td>${r.clicks}</td><td>${r.goal_count}</td><td>${r.goal_rate}%</td><td>${r.spend.toFixed(2)}</td></tr>`).join('')||'<tr><td colspan="6">Generate drafts to start comparing variants.</td></tr>'}</tbody></table></div>${results.recommendations.map(r=>`<p class="recommendation">${esc(r)}</p>`).join('')}<p class="helper">${esc(results.benchmark_note)}</p></div>${c.variants.length?`<div class="panel"><div class="panel-heading"><div><h3>Record daily results</h3><p class="helper">Saving replaces this variant’s figures for the selected date.</p></div><label>Date<input id="metric-date" type="date" value="${state.metricDate || localDate()}" max="${localDate()}"></label></div><div class="grid2">${c.variants.map(v=>metricForm(v,snapshots,state.metricDate||localDate())).join('')}</div></div>`:''}`;
}
function localDate(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function metricForm(v,snapshots,period){const row=snapshots.find(s=>s.variant_id===v.id&&s.period===period)||{};return `<form class="metric-form" data-metric="${v.id}" data-period="${esc(period)}"><h3>${esc(v.label)}</h3><div class="metric-fields">${[['delivered','Delivered'],['clicks','Clicks'],['conversions','Conversions'],['replies','Replies'],['signups','Signups'],['opt_outs','Opt-outs'],['complaints','Complaints'],['spend','Spend (your currency)']].map(([key,label])=>metricInput(key,label,row[key]||0)).join('')}</div><div class="actions" style="margin-top:18px"><button class="primary">Save results</button><span class="muted metric-saved"></span></div><div class="form-error" role="alert"></div></form>`;}
async function inboxView(){
  const messages=await api('/api/studio/inbox');
  return `<div class="toolbar"><h3>Latest conversations</h3><button class="secondary" data-action="sync">↻ Sync Telegram</button></div><p class="helper">Replies are assigned to the most recent campaign delivered to that contact. The latest 300 messages appear here.</p>${messages.length?messages.map(m=>`<article class="inbox-item ${m.direction==='out'?'out':''}"><div class="inbox-meta"><div><strong>${esc(m.direction==='out'?'You':m.name||'Telegram contact')}</strong> · ${esc(m.campaign)}</div><time>${date(m.created_at,true)}</time></div><p>${esc(m.text)}</p>${m.direction==='in'&&m.active?`<button class="small-button" data-reply="${m.contact_id}" data-name="${esc(m.name)}">Reply →</button>`:''}${!m.active?'<span class="muted">Unsubscribed</span>':''}</article>`).join(''):empty('Room for good conversations','Replies to campaign messages will appear here. Send a campaign, then sync Telegram.')}`;
}
function connectionView(){
  const c=state.connection || {};
  return `<div class="grid2"><div class="panel"><div class="panel-heading"><h3>Your Telegram bot</h3>${badge(c.connected?'connected':'offline')}</div><div class="facts">${field('Bot',c.username?'@'+c.username:'Not connected')}${field('Display name',c.name)}${field('Environment',c.environment)}${field('Incoming messages',c.webhook_enabled?'External webhook is active':'Local polling while server runs')}</div>${c.detail?`<p class="notice">${esc(c.detail)}</p>`:''}${c.webhook_enabled?'<p class="notice">An external webhook is active. Local polling cannot run until you remove it in your bot configuration.</p>':''}<p class="helper">Your token stays on the server. For use inside Telegram, host this app over HTTPS and set its Mini App URL in BotFather. Campaign management is limited to the owner configured in CHAT_ID.</p><button class="secondary" data-action="reconnect">↻ Check connection</button></div><div class="panel"><h3>Send yourself a test</h3><p class="helper">A quick connection check, separate from campaign delivery.</p><form id="test-form">${state.session?.user.is_local?`<label>Your private chat ID<input name="chat_id" type="number" min="1" step="1" required value="${esc(c.default_chat_id||'')}"></label>`:''}<label>Message<textarea name="text" rows="3" required maxlength="4096"></textarea></label><button class="primary" ${c.connected?'':'disabled'}>Send test message</button><div class="form-error" role="alert"></div></form></div></div><div class="panel"><h3>How the workspace works</h3><div class="grid3"><div><p><strong>01 · Plan</strong></p><p class="helper">Write a specific brief, generate drafts from your saved templates, and refine your message.</p></div><div><p><strong>02 · Connect</strong></p><p class="helper">Invite subscribers or add contacts with explicit consent. STOP requests unsubscribe them from all campaigns.</p></div><div><p><strong>03 · Learn</strong></p><p class="helper">Launch or schedule a saved message, monitor delivery, and record outcomes to compare variants.</p></div></div></div>`;
}

function openEditor(c=null){
  const form=$('campaign-form');form.reset();form.elements.campaign_id.value=c?.id||'';
  $('editor-title').textContent=c?'Edit campaign brief':'Create a campaign';$('editor-error').textContent='';
  for(const el of form.elements){if(!el.name||el.name==='campaign_id')continue;if(c&&c[el.name]!==undefined){if(el.type==='checkbox')el.checked=Boolean(c[el.name]);else el.value=c[el.name];}}
  if(!c){form.elements.deadline.value=localDate();form.elements.audience_size.value='';form.elements.target_value.value='';}
  $('editor').showModal();
}
function confirm(title,body,label,callback){$('confirm-title').textContent=title;$('confirm-body').innerHTML=body;$('confirm-action').textContent=label;$('confirm-error').textContent='';confirmCallback=callback;$('confirm-dialog').showModal();}
async function refresh(){await Promise.all([loadCampaigns(),loadStats()]);await render();}
async function sync(){const d=await post('/api/studio/sync');toast(`Telegram synced · ${d.processed} updates processed`);await refresh();}

document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.id==='confirm-action')return;
  if(b.dataset.reportExport||b.dataset.reportRefresh||b.dataset.directHistoryRefresh)return;
  if(b.dataset.insightAction)return;
  try{
    if(b.dataset.close){$(b.dataset.close).close();return;}
    if(b.dataset.page){state.page=b.dataset.page;state.detail=null;await render();return;}
    if(b.dataset.open){state.selected=Number(b.dataset.open);state.detail={};state.tab='brief';await render();return;}
    if(b.dataset.tab){state.tab=b.dataset.tab;await render();return;}
    if(b.dataset.filter){state.filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(el=>el.classList.toggle('active',el.dataset.filter===state.filter));filterCards();return;}
    if(b.id==='new-campaign'||b.dataset.action==='new'){openEditor();return;}
    if(b.dataset.action==='edit'){openEditor(state.detail);return;}
    if(b.dataset.action==='back'){state.detail=null;await refresh();return;}
    if(b.dataset.action==='refresh'){await refresh();return;}
    if(b.dataset.action==='generate'){b.disabled=true;const result=await post(`/api/campaigns/${state.selected}/drafts`);toast(`${result.variants.length} message drafts created`);await render();return;}
    if(b.dataset.action==='sync'){b.disabled=true;await sync();return;}
    if(b.dataset.action==='reconnect'){b.disabled=true;state.connection=await api('/api/studio/connection');updateConnection();await render();return;}
    if(b.dataset.action==='copy-link'){try{await navigator.clipboard.writeText($('join-link').value);toast('Invite link copied');}catch{$('join-link').select();toast('Select and copy the invite link');}return;}
    if(b.dataset.action==='export'){const kind=b.dataset.exportKind||'contacts';const r=await fetch(`/api/studio/campaigns/${state.selected}/export?kind=${kind}`,{headers:{'X-Telegram-Init-Data':tg?.initData||''}});if(!r.ok)throw new Error('Could not export CSV');const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=`campaign-${state.selected}-${kind}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);return;}
    if(b.dataset.unsubscribe){confirm('Unsubscribe contact','<p>This contact will be removed from pending sends for this campaign.</p>','Unsubscribe',async()=>{await api(`/api/studio/audience/${b.dataset.unsubscribe}`,{method:'DELETE'});await refresh();});return;}
    if(b.dataset.contactNotes){confirm('Contact notes',`<label>Notes<textarea id="contact-notes" rows="5" maxlength="4000">${esc(b.dataset.notes)}</textarea></label>`,'Save notes',async()=>{await api(`/api/studio/audience/${b.dataset.contactNotes}`,{method:'PATCH',body:JSON.stringify({stage:b.dataset.contactStage,notes:$('contact-notes').value})});await render();});return;}
    if(b.dataset.reply){confirm(`Reply to ${b.dataset.name||'contact'}`,'<label>Message<textarea id="reply-text" rows="5" maxlength="4096" required></textarea></label>','Send reply',async()=>{const text=$('reply-text').value.trim();if(!text)throw new Error('Enter a reply.');await post(`/api/studio/audience/${b.dataset.reply}/reply`,{text});toast('Reply sent');await render();});return;}
    if(b.dataset.job){const action=b.dataset.jobAction;confirm(`${action[0].toUpperCase()+action.slice(1)} this send?`,`<p>${action==='cancel'?'Remaining messages will be cancelled.':action==='pause'?'Remaining messages will wait until you resume.':'Remaining messages will continue when their schedule is due.'} A message already in flight can still finish.</p>`,action[0].toUpperCase()+action.slice(1),async()=>{await post(`/api/studio/broadcasts/${b.dataset.job}/${action}`);await refresh();});return;}
    if(b.dataset.testVariant){const form=b.closest('form');const values=new FormData(form);const text=['attention','interest','desire','action'].map(k=>values.get(k)).join('\n\n');const chat=state.connection?.default_chat_id;confirm('Send a preview to yourself',`<p class="helper">This sends the current draft to your configured private chat.</p><div class="message-preview">${esc(text)}</div>`,'Send preview',async()=>{await post('/api/telegram/test-send',{text,...(state.session.user.is_local?{chat_id:Number(chat)}:{})});toast('Preview sent to your Telegram');});return;}
  }catch(e){toast(e.message,true);}finally{if(b.isConnected)b.disabled=false;}
});
$('confirm-action').addEventListener('click',async()=>{const button=$('confirm-action');button.disabled=true;try{await confirmCallback();$('confirm-dialog').close();}catch(e){$('confirm-error').textContent=e.message;}finally{button.disabled=false;}});
$('campaign-select').addEventListener('change',async e=>{state.selected=Number(e.target.value);state.metricDate=null;state.detail=null;await render();});
document.addEventListener('input',e=>{
  if(e.target.id==='campaign-search'){state.search=e.target.value;filterCards();}
  const form=e.target.closest('[data-variant]');if(form){const preview=form.querySelector('[data-preview]');preview.textContent=['attention','interest','desire','action'].map(k=>form.elements[k].value).join('\n\n');}
});
document.addEventListener('change',async e=>{
  try{
    if(e.target.dataset.stage){await api(`/api/studio/audience/${e.target.dataset.stage}`,{method:'PATCH',body:JSON.stringify({stage:e.target.value,notes:e.target.dataset.notes})});toast('Contact stage updated');}
    if(e.target.id==='metric-date'){if(!e.target.value)return;state.metricDate=e.target.value;document.querySelectorAll('[data-metric]').forEach(form=>{const v=state.reportVariants.find(v=>v.id===Number(form.dataset.metric));form.outerHTML=metricForm(v,state.snapshots,state.metricDate);});}
  }catch(error){toast(error.message,true);}
});
document.addEventListener('submit',async event=>{
  const form=event.target;event.preventDefault();
  if(['conversion-form','insight-filters','bot-settings-form','direct-send-form','workspace-form'].includes(form.id))return;
  const button=form.querySelector('[type="submit"]')||form.querySelector('button:not([type="button"])');
  const error=form.querySelector('.form-error');if(error)error.textContent='';if(button)button.disabled=true;
  try{
    const data=Object.fromEntries(new FormData(form));
    if(form.id==='campaign-form'){
      const id=data.campaign_id;delete data.campaign_id;data.audience_size=Number(data.audience_size);data.target_value=Number(data.target_value);data.budget=Number(data.budget||0);data.consent_confirmed=form.elements.consent_confirmed.checked;
      const result=await api(id?`/api/campaigns/${id}`:'/api/campaigns',{method:id?'PUT':'POST',body:JSON.stringify(data)});
      $('editor').close();state.selected=result.id;state.page='campaigns';state.detail={};state.tab='brief';await refresh();toast(id?'Brief updated. Existing drafts are preserved.':'Campaign created');
    }else if(form.dataset.variant){
      const result=await api(`/api/campaigns/${state.selected}/variants/${form.dataset.variant}`,{method:'PUT',body:JSON.stringify(data)});
      const i=state.detail.variants.findIndex(v=>v.id===result.id);state.detail.variants[i]=result;toast('Message draft saved');
    }else if(form.id==='contact-form'){
      await post(`/api/studio/campaigns/${state.selected}/audience`,{chat_id:Number(data.chat_id),name:data.name,consent_confirmed:form.elements.consent_confirmed.checked});await refresh();toast('Contact added');
    }else if(form.id==='launch-form'){
      const variant=state.detail.variants.find(v=>v.id===Number(data.variant_id));
      const when=data.scheduled_at?new Date(data.scheduled_at).toISOString():null;
      const audience=await api(`/api/studio/campaigns/${state.selected}/audience`);const count=audience.subscribers.filter(s=>s.active).length;
      confirm('Review your campaign send',`<p><strong>${count} subscribed contacts</strong> will receive ${esc(variant.label)} ${when?'on '+esc(new Date(when).toLocaleString()):'now'}.</p><p class="helper">This uses the saved message below. Any unsaved edits must be saved first.</p><div class="message-preview">${esc(variant.copy)}</div>`,'Confirm launch',async()=>{const result=await post(`/api/studio/campaigns/${state.selected}/launch`,{variant_id:variant.id,scheduled_at:when,confirmed:true});toast(`Send #${result.id} queued for ${result.recipients} contacts`);await refresh();});
    }else if(form.dataset.metric){
      for(const key of Object.keys(data))data[key]=Number(data[key]);data.variant_id=Number(form.dataset.metric);data.period=form.dataset.period;
      await post(`/api/campaigns/${state.selected}/metrics`,data);
      // Keep other variants' unsaved fields intact after this save.
      const index=state.snapshots.findIndex(s=>s.variant_id===data.variant_id&&s.period===data.period);if(index>=0)state.snapshots[index]=data;else state.snapshots.push(data);
      form.querySelector('.metric-saved').textContent='Saved';toast('Results saved. Refresh insights when you finish entering results.');
    }else if(form.id==='test-form'){
      if(data.chat_id)data.chat_id=Number(data.chat_id);await post('/api/telegram/test-send',data);toast('Test message sent. Check Telegram.');
    }
  }catch(e){if(error)error.textContent=e.message;else toast(e.message,true);}finally{if(button?.isConnected)button.disabled=false;}
});
function updateConnection(){const c=state.connection;$('connection-pill').textContent=c?.connected?`@${c.username} connected`:'Bot offline';}
async function start(){
  try{
    state.session=await api('/api/session');applyWorkspace(await api('/api/studio/workspace'));$('profile-name').textContent=state.session.user.first_name || 'Your workspace';$('profile-mode').textContent=state.session.user.is_local?'Local workspace':'Telegram Mini App';
    await Promise.all([loadCampaigns(),loadStats()]);
    state.connection=await api('/api/studio/connection');updateConnection();
    const hash=location.hash.slice(1);if(labels[hash])state.page=hash;
    await render();
  }catch(e){$('page-content').innerHTML=empty('Workspace unavailable',e.message);$('connection-pill').textContent='Connection needed';}
}
window.addEventListener('hashchange',()=>{const page=location.hash.slice(1);if(labels[page]){state.page=page;state.detail=null;render();}});
// Poll delivery history only; never replace a form while someone is editing it.
setInterval(async()=>{if(document.hidden||document.querySelector('dialog[open]'))return;try{if(state.page==='campaigns'&&state.detail&&state.tab==='delivery'){const id=state.selected;const jobs=await api(`/api/studio/campaigns/${id}/deliveries`);if(state.selected===id&&state.tab==='delivery'&&$('delivery-history'))$('delivery-history').innerHTML=deliveryHistory(jobs);await loadStats();}}catch{/* User can explicitly refresh after a transient error. */}},5000);
start();
