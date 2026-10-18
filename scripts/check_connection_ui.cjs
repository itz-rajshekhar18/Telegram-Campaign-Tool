// Verify form/review/request/result behavior without sending network messages.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
let submit, review, calls=[];
const button={disabled:false,isConnected:true};
const error={textContent:''}, results={textContent:'',innerHTML:''}, saved={textContent:''};
const elements={bot_token:{value:'123456:temporary-token'},owner_chat_id:{value:'12345'},chats:{value:'12345, 12345\n-1001234567890'},text:{value:'Hello <team>'}};
const form={id:'direct-send-form',elements,querySelector:s=>s==='[type="submit"]'?button:s==='.form-error'?error:s==='[data-settings-result]'?saved:results};
const context={console,state:{workspace:{limits:{direct_recipients:20}}},document:{addEventListener:(event,fn)=>{if(event==='submit')submit=fn;}},
  esc:s=>String(s??'').replaceAll('<','&lt;'),badge:s=>s,toast:()=>{},
  confirm:(title,body,label,fn)=>{review={body,fn};},
  api:async(path,options)=>{calls.push({path,options});return options?{username:'demo',restart_required:true}:{token_saved:true,owner_chat_id:'12345',restart_required:false};},
  post:async(path,payload)=>{calls.push({path,payload});return {sent:1,results:[{chat_id:'12345',status:'sent',message_id:7},{chat_id:'-1001234567890',status:'failed',detail:'Rejected'}]};}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/connection.js','utf8'),context);
(async()=>{
  const html=await context.connectionToolsView();
  assert(html.includes('bot-settings-form')&&html.includes('direct-send-form'));
  calls=[];
  await submit({target:form,preventDefault(){}});
  assert.equal(calls.length,0);assert(review.body.includes('2 unique chats'));assert(review.body.includes('Hello &lt;team>'));
  await review.fn();
  assert.equal(calls[0].path,'/api/studio/direct-send');
  assert.deepEqual(Array.from(calls[0].payload.chat_ids),['12345','-1001234567890']);
  assert.equal(calls[0].payload.bot_token,'123456:temporary-token');
  assert.equal(elements.bot_token.value,'');assert(results.innerHTML.includes('1 of 2 sent'));assert.equal(button.disabled,false);
  await assert.rejects(review.fn(),/Close this review/);
  form.id='bot-settings-form';elements.bot_token.value='123456:new-token';
  await submit({target:form,preventDefault(){}});
  assert.equal(calls.at(-1).path,'/api/studio/settings');assert.equal(calls.at(-1).options.method,'PUT');
  assert.equal(elements.bot_token.value,'');assert(saved.textContent.includes('Restart the server'));
  console.log('PASS: frontend settings, review before send, deduplication, temporary token clearing, per-chat results and duplicate submission guard');
})().catch(error=>{console.error(error);process.exitCode=1;});
