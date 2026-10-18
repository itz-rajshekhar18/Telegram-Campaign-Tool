/* Backend metadata is the source for business defaults and supported form options. */
function applyWorkspace(metadata){
  state.workspace=metadata;
  configureReportRefresh(metadata.settings.report_refresh_seconds);
  const defaults=metadata.settings;
  document.querySelectorAll('[data-backend-options]').forEach(select=>{
    select.innerHTML=metadata.options[select.dataset.backendOptions].map(value=>`<option value="${esc(value)}">${esc(value.replaceAll('_',' '))}</option>`).join('');
  });
  for(const id of ['campaign-form','conversion-form']){
    const form=$(id);if(!form)continue;
    for(const key of ['category','currency','language','tone','source']){
      const element=form.elements[key],value=defaults['default_'+key];
      if(element&&value!==undefined){element.defaultValue=value;element.value=value;}
    }
  }
  for(const page of ['analytics','reports']){
    insightState[page].currency=defaults.default_currency;
  }
}
function workspacePanel(){
  const s=state.workspace.settings;
  const input=(key,label,type='text',attributes='')=>`<label>${label}<input name="${key}" type="${type}" value="${esc(s[key])}" required ${attributes}></label>`;
  return `<div class="panel"><h3>Workspace defaults & reporting rules</h3><p class="helper">These settings are saved on the backend and apply to new forms and future draft generation. Existing campaigns and messages retain their saved values.</p><form id="workspace-form"><div class="grid2">${input('default_category','Default category','text','maxlength="80"')}${input('default_currency','Default currency','text','pattern="[A-Z]{3}" maxlength="3"')}${input('default_language','Default language','text','maxlength="80"')}${input('default_tone','Default tone','text','maxlength="80"')}${input('default_source','Default conversion source','text','maxlength="80"')}${input('opt_out_alert_percent','Opt-out alert (%)','number','min="0.01" max="100" step="0.01"')}${input('comparison_min_delivered','Minimum delivered for comparison','number','min="1" max="100000000"')}${input('report_days','Default report range (days)','number','min="1" max="366"')}${input('report_refresh_seconds','Report refresh interval (seconds)','number','min="10" max="3600"')}</div><details><summary>Message draft templates</summary><p class="helper">Use these fields in braces: ${state.workspace.template_fields.map(k=>`{${esc(k)}}`).join(', ')}. STOP instructions are appended automatically. Templates interpolate saved campaign data; this is not AI generation.</p>${s.draft_templates.map((template,i)=>`<fieldset data-template-index="${i}"><legend>${esc(template.label)}</legend>${['label','attention','interest','desire','action'].map(key=>`<label>${key}<textarea data-template-field="${key}" rows="2" required maxlength="${key==='label'?120:1000}">${esc(template[key])}</textarea></label>`).join('')}</fieldset>`).join('')}</details><button class="primary" type="submit">Save workspace settings</button><div class="form-error" role="alert"></div><p class="helper" data-workspace-result role="status"></p></form></div>`;
}
document.addEventListener('submit',async event=>{
  const form=event.target;if(form.id!=='workspace-form')return;
  event.preventDefault();const button=form.querySelector('[type="submit"]'),error=form.querySelector('.form-error');button.disabled=true;error.textContent='';
  try{
    const values=Object.fromEntries(new FormData(form));
    for(const key of ['opt_out_alert_percent','comparison_min_delivered','report_days','report_refresh_seconds'])values[key]=Number(values[key]);
    values.draft_templates=[...form.querySelectorAll('[data-template-index]')].map(group=>Object.fromEntries([...group.querySelectorAll('[data-template-field]')].map(el=>[el.dataset.templateField,el.value])));
    const metadata=await api('/api/studio/workspace',{method:'PUT',body:JSON.stringify(values)});
    applyWorkspace(metadata);form.querySelector('[data-workspace-result]').textContent='Workspace settings saved. New forms and future drafts will use these values.';toast('Workspace settings saved');
  }catch(e){error.textContent=e.message;}finally{button.disabled=false;}
});

async function directHistoryView(){
  const rows=await api('/api/studio/direct-history');
  return `<div class="panel"><div class="panel-heading"><h3>Direct-send history</h3><button class="small-button" data-direct-history-refresh="true">Refresh history</button></div><p class="helper">Latest saved batches, including sends through temporary bot tokens. Tokens are never stored. Unknown results require checking Telegram before resending.</p><div id="direct-history-list">${directHistoryRows(rows)}</div></div>`;
}
function directHistoryRows(rows){return rows.length?rows.map(row=>`<details><summary>Batch #${row.id} · Bot ${esc(row.bot_id)} · ${date(row.created_at,true)}</summary><div class="message-preview">${esc(row.text)}</div><div class="table-wrap"><table><thead><tr><th>Chat</th><th>Status</th><th>Details</th></tr></thead><tbody>${row.results.map(r=>`<tr><td>${esc(r.chat_id)}</td><td>${badge(r.status)}</td><td>${esc(r.detail||r.message_id||'')}</td></tr>`).join('')}</tbody></table></div></details>`).join(''):empty('No direct sends yet','Your send results will be saved here automatically.');}
async function downloadAnalyticsCSV(kind){
  const query=insightQuery(insightDates(insightState.analytics));
  const response=await fetch('/api/studio/report-export?'+query+'&kind='+kind,{headers:{'X-Telegram-Init-Data':tg?.initData||''}});
  if(!response.ok)throw new Error('Could not export saved analytics.');
  downloadBlob(await response.blob(),`analytics-${kind}.csv`);
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-direct-history-refresh]');if(!button)return;
  button.disabled=true;try{const rows=await api('/api/studio/direct-history');$('direct-history-list').innerHTML=directHistoryRows(rows);}catch(e){toast(e.message,true);}finally{button.disabled=false;}
});
