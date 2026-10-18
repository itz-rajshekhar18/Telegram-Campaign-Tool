const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const listeners={};let refreshSeconds,exportURL,posted;
const values={default_category:'custom',default_currency:'JPY',default_language:'Japanese',default_tone:'brief',default_source:'owned-site',
  opt_out_alert_percent:10,comparison_min_delivered:1,report_days:7,report_refresh_seconds:15,
  draft_templates:[{label:'Custom draft',attention:'{product}',interest:'{benefit}',desire:'{proof}',action:'{cta}'}]};
const meta={settings:values,currencies:['JPY'],template_fields:['product','benefit','proof','cta'],options:{primary_metrics:['conversions'],conversion_statuses:['confirmed'],conversion_kinds:['purchase']}};
const campaign={elements:Object.fromEntries(['category','currency','language','tone'].map(k=>[k,{value:'',defaultValue:''}]))};
const conversion={elements:Object.fromEntries(['currency','source'].map(k=>[k,{value:'',defaultValue:''}]))};
const primary={dataset:{backendOptions:'primary_metrics'},innerHTML:''};
const button={disabled:false},error={textContent:''},saved={textContent:''};
const fieldsets=values.draft_templates.map(t=>({querySelectorAll:()=>Object.entries(t).map(([key,value])=>({dataset:{templateField:key},value}))}));
const form={id:'workspace-form',querySelector:s=>s==='[type="submit"]'?button:s==='.form-error'?error:saved,querySelectorAll:()=>fieldsets};
const context={console,URLSearchParams,state:{},insightState:{analytics:{},reports:{}},tg:null,
  $:id=>id==='campaign-form'?campaign:id==='conversion-form'?conversion:null,
  configureReportRefresh:seconds=>{refreshSeconds=seconds;},
  document:{querySelectorAll:()=>[primary],addEventListener:(type,fn)=>{listeners[type]=fn;}},
  FormData:class{[Symbol.iterator](){return Object.entries(values).filter(([key])=>key!=='draft_templates')[Symbol.iterator]();}},
  esc:v=>String(v??'').replaceAll('<','&lt;'),toast:()=>{},money:()=>'',date:()=>'',badge:v=>v,empty:(t,d)=>t+d,
  api:async(path,options)=>{posted={path,payload:JSON.parse(options.body)};return meta;},
  insightDates:v=>v,insightQuery:v=>new URLSearchParams(v),fetch:async url=>{exportURL=url;return {ok:true,blob:async()=>({})};},downloadBlob:()=>{}};
vm.createContext(context);vm.runInContext(fs.readFileSync('static/workspace.js','utf8'),context);
(async()=>{
  context.applyWorkspace(meta);
  assert.equal(campaign.elements.currency.defaultValue,'JPY');assert.equal(conversion.elements.source.value,'owned-site');
  assert(primary.innerHTML.includes('conversions'));assert.equal(refreshSeconds,15);assert.equal(context.insightState.reports.currency,'JPY');
  assert(context.workspacePanel().includes('Custom draft'));assert(context.workspacePanel().includes('Save workspace settings'));
  await listeners.submit({target:form,preventDefault(){}});
  assert.equal(posted.path,'/api/studio/workspace');assert.equal(posted.payload.default_currency,'JPY');assert.equal(posted.payload.draft_templates[0].attention,'{product}');
  assert(saved.textContent.includes('saved'));assert.equal(button.disabled,false);
  context.insightState.analytics={currency:'JPY',start:'2026-10-01',end:'2026-10-18'};
  await context.downloadAnalyticsCSV('daily');assert(exportURL.includes('/api/studio/report-export?')&&exportURL.includes('currency=JPY')&&exportURL.includes('kind=daily'));
  const html=context.directHistoryRows([{id:1,bot_id:'999',created_at:'now',text:'<private message>',results:[{chat_id:'123',status:'unknown',detail:'Check Telegram'}]}]);
  assert(html.includes('&lt;private message>')&&html.includes('unknown'));
  console.log('PASS: backend defaults and form options, saved template editor, configurable report timer, backend analytics CSV and persisted direct history rendering');
})().catch(e=>{console.error(e);process.exitCode=1;});
