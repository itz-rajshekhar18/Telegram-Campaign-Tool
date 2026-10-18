// Simulated frontend checks; no browser or real network traffic.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let click,poll,exportURL,downloaded,apiCalls=[];
const filters={campaign_id:'7',currency:'INR',start:'2026-10-18',end:'2026-10-18'};
const data={id:4,saved_at:'2026-10-18T00:00:00Z',analytics:{start:filters.start,end:filters.end,currency:'INR',
  live:{sent:2,replies:0,failed:0,unknown:0},manual:{delivered:0,clicks:0,conversions:0},
  ledger:{confirmed:1,revenue:20},finance:{spend:0,roas:null},trend:[],campaigns:[]},
  conversions:{total:1,summary:{INR:{confirmed:1,pending:0,refunded:0,cancelled:0,revenue:20}},items:[{name:'<unsafe>',campaign_name:'My campaign',status:'confirmed',amount:20,currency:'INR',source:'telegram',occurred_on:filters.start}]}};
const output={dataset:{query:new URLSearchParams(filters).toString()},isConnected:true,innerHTML:''};
const error={textContent:''};
const context={console,URLSearchParams,insightState:{},state:{page:'reports'},tg:null,
  document:{hidden:false,addEventListener:(type,fn)=>{if(type==='click')click=fn;},querySelector:()=>null},
  setInterval:fn=>{poll=fn;},clearInterval:()=>{},$:(id)=>id==='report-output'?output:error,
  insightQuery:f=>new URLSearchParams(f),insightDates:f=>f.start?f:Object.assign(f,filters),
  insightFilters:page=>'<form>'+page+'</form>',esc:s=>String(s??'').replaceAll('<','&lt;'),
  insightCard:(l,v,d)=>`${l}:${v}:${d}`,field:(l,v)=>`${l}:${v}`,badge:s=>s,money:(v,c)=>`${c} ${v}`,
  trendChart:()=>'<svg/>',empty:(t,d)=>`${t}:${d}`,toast:()=>{},
  api:async url=>{apiCalls.push(url);return data;},
  fetch:async url=>{exportURL=url;return {ok:true,blob:async()=>({csv:true})};},
  downloadBlob:(blob,name)=>{downloaded=name;}};
vm.createContext(context);vm.runInContext(fs.readFileSync('static/reports.js','utf8'),context);
(async()=>{
  Object.assign(context.insightState.reports,filters);
  context.configureReportRefresh(30);
  const before=JSON.stringify(data);
  const html=await context.automaticReportsView();
  assert(html.includes('Auto-saved report #4')&&html.includes('&lt;unsafe>'));
  assert(html.includes('Analytics summary')&&html.includes('Latest 10 of 1'));
  assert.equal(JSON.stringify(data),before);
  for(const kind of ['summary','daily','campaigns','conversions']){
    const button={dataset:{reportExport:kind},disabled:false,isConnected:true};
    await click({target:{closest:()=>button}});
    assert.equal(exportURL,'/api/studio/report-export?'+output.dataset.query+'&kind='+kind);
    assert.equal(downloaded,`report-${kind}.csv`);assert.equal(button.disabled,false);
  }
  // Edits to unsubmitted filter state cannot change refresh/export scope.
  context.insightState.reports.campaign_id='999';
  await context.refreshAutomaticReport();assert.equal(apiCalls.at(-1),'/api/studio/reports?'+output.dataset.query);
  assert(output.innerHTML.includes('Auto-saved report #4'));
  context.api=async()=>{throw new Error('offline');};
  const prior=output.innerHTML;await context.refreshAutomaticReport();
  assert.equal(output.innerHTML,prior);assert(error.textContent.includes('previous report'));
  context.state.page='analytics';const count=apiCalls.length;poll();assert.equal(apiCalls.length,count);
  console.log('PASS: automatic report rendering, source preservation, applied-filter CSV exports, refresh scope, error preservation and inactive-page polling');
})().catch(e=>{console.error(e);process.exitCode=1;});
