/* Owner credentials stay on the server; temporary bot tokens are never stored in the browser. */
async function connectionToolsView(){
  const s=await api('/api/studio/settings');
  return `<div class="panel"><h3>Bot credentials & owner ID</h3><p class="helper">Enter your BotFather token and your positive private Telegram user ID. Saved values update the server’s private .env file. Blank token keeps the saved token. Restart the server after saving to apply changes to campaigns and Mini App sign-in.</p><p class="notice">${s.restart_required?'Saved settings are waiting for a server restart.':'One workspace manages campaigns for one bot. Use the direct sender below to send through another bot.'}</p><form id="bot-settings-form" autocomplete="off"><div class="grid2"><label>TELEGRAM_BOT_TOKEN<input name="bot_token" type="password" autocomplete="new-password" maxlength="200" placeholder="${s.token_saved?'Token saved · leave blank to keep it':'123456789:BotFather-token'}" ${s.token_saved?'':'required'}></label><label>CHAT_ID · workspace owner<input name="owner_chat_id" inputmode="numeric" pattern="[1-9][0-9]{0,15}" required value="${esc(s.owner_chat_id)}" placeholder="Your positive private user ID"></label></div><p class="helper">API_ID and API_HASH belong to Telegram’s legacy user-account API. Bot messaging only needs a bot token and destination IDs.</p><button class="primary" type="submit">Verify & save settings</button><p class="helper" data-settings-result role="status"></p><div class="form-error" role="alert"></div></form></div>
  <div class="panel"><h3>Send to one or multiple chats</h3><p class="helper">Send a plain-text message to up to ${state.workspace.limits.direct_recipients} chats at a time. Use numeric private IDs, negative group IDs, or public @channel usernames. Private recipients must first start your bot; groups and channels must allow it to post. For campaign subscriptions and STOP handling, use Audience and Delivery.</p><form id="direct-send-form" autocomplete="off"><label>Bot token <span class="optional">optional · leave blank to use the active workspace bot</span><input name="bot_token" type="password" autocomplete="new-password" maxlength="200" placeholder="Temporary token for your other bot"></label><label>Destination chat IDs<textarea name="chats" rows="4" maxlength="1500" required placeholder="123456789&#10;-1001234567890&#10;@your_channel"></textarea><span class="helper">One per line, or separate with commas or spaces. Duplicate IDs are sent once.</span></label><label>Message<textarea name="text" rows="5" required maxlength="4096" placeholder="Write the message to send to every listed chat"></textarea></label><label class="check"><input name="permission" type="checkbox" required><span>I own these chats or have permission from their recipients to send this message.</span></label><button class="primary" type="submit">Review message & recipients</button><div class="form-error" role="alert"></div><div data-direct-results role="status"></div></form></div>`;
}

let directSending=false;
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(!['bot-settings-form','direct-send-form'].includes(form.id))return;
  event.preventDefault();
  const button=form.querySelector('[type="submit"]');
  const error=form.querySelector('.form-error');error.textContent='';button.disabled=true;
  try{
    if(form.id==='bot-settings-form'){
      const result=await api('/api/studio/settings',{method:'PUT',body:JSON.stringify({bot_token:form.elements.bot_token.value.trim(),owner_chat_id:form.elements.owner_chat_id.value.trim()})});
      form.elements.bot_token.value='';form.elements.bot_token.required=false;form.elements.bot_token.placeholder='Token saved · leave blank to keep it';
      form.querySelector('[data-settings-result]').textContent=`Verified @${result.username}. ${result.restart_required?'Settings saved. Restart the server with python -m app.main to apply them. If you change the owner ID, reopen the Mini App as that owner.':'Settings saved and already active.'}`;
      toast('Bot settings saved');
    }else{
      if(directSending)throw new Error('A direct send is already in progress.');
      const chats=[...new Map(form.elements.chats.value.trim().split(/[\s,;]+/).filter(Boolean).map(v=>[v.toLowerCase(),v])).values()];
      if(!chats.length||chats.length>state.workspace.limits.direct_recipients)throw new Error(`Enter between 1 and ${state.workspace.limits.direct_recipients} unique destination IDs.`);
      if(chats.some(v=>!/^(-?[1-9][0-9]{0,15}|@[A-Za-z][A-Za-z0-9_]{4,31})$/.test(v)))throw new Error('Use numeric chat IDs or public @channel usernames.');
      const text=form.elements.text.value;if(!text.trim())throw new Error('Enter a message.');
      let attempted=false;
      // Read the token only when send is confirmed; do not retain it in a dialog callback.
      confirm('Review direct send',`<p><strong>${chats.length} unique chat${chats.length===1?'':'s'}</strong> will receive this message through ${form.elements.bot_token.value.trim()?'the temporary bot token you entered':'the active workspace bot'}.</p><div class="message-preview">${esc(chats.join('\n'))}</div><div class="message-preview">${esc(text)}</div><p class="helper">This batch saves per-chat results to direct-send history and remains separate from campaign history. Keep this page open until results arrive. If the connection drops, refresh direct-send history and check Telegram before resending.</p>`,'Send now',async()=>{
        if(attempted)throw new Error('Close this review and check the results before reviewing another send.');
        if(directSending)throw new Error('A direct send is already in progress.');
        attempted=true;
        directSending=true;button.disabled=true;
        const results=form.querySelector('[data-direct-results]');results.textContent='Sending… Keep this page open.';
        try{
          const payload={chat_ids:chats,text,confirmed:true,bot_token:form.elements.bot_token.value.trim()};
          form.elements.bot_token.value='';
          const result=await post('/api/studio/direct-send',payload);
          results.innerHTML=`<p><strong>${result.sent} of ${result.results.length} sent</strong></p><div class="table-wrap"><table><thead><tr><th>Chat</th><th>Status</th><th>Details</th></tr></thead><tbody>${result.results.map(r=>`<tr><td>${esc(r.chat_id)}</td><td>${badge(r.status)}</td><td class="wrap">${esc(r.message_id?'Message #'+r.message_id:r.detail)}${r.retry_after?` Wait ${esc(r.retry_after)} seconds before trying again.`:''}</td></tr>`).join('')}</tbody></table></div>`;
          toast(`Direct send finished · ${result.sent} sent`);
        }catch(e){results.textContent='Results unavailable. Check every destination in Telegram before resending.';throw e;}
        finally{directSending=false;if(button.isConnected)button.disabled=false;}
      });
    }
  }catch(e){error.textContent=e.message;}
  finally{if(button.isConnected&&!directSending)button.disabled=false;}
});
