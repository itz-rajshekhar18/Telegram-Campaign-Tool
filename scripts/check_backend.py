"""Exercise the application workflow against a temporary backend; Telegram is simulated."""
import asyncio
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def check(directory):
    os.environ.update(APP_ENV='development', TELEGRAM_BOT_TOKEN='123456:'+'a'*30, CHAT_ID='12345',
                      DATABASE_URL=f'sqlite+aiosqlite:///{directory.as_posix()}/backend.db')
    import httpx
    from app.main import app
    from app.database import init_db, async_session, engine
    from app.models import Campaign, DirectSendRecord
    from app import studio, connection
    from app.telegram_auth import authenticated_user
    await init_db()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        metadata=(await client.get('/api/studio/workspace')).json()
        settings=metadata['settings']
        settings.update(default_currency='JPY',default_language='Japanese',default_tone='brief',default_category='custom category',
                        default_source='owned-site',opt_out_alert_percent=10,comparison_min_delivered=1,report_days=7,report_refresh_seconds=15)
        settings['draft_templates']=[{'label':f'Approach {i}','attention':'{product}: {benefit}', 'interest':'For {audience}',
                                      'desire':'{proof} {offer}', 'action':'{cta}'} for i in range(3)]
        response=await client.put('/api/studio/workspace',json=settings);assert response.status_code==200,response.text
        assert (await client.get('/api/studio/workspace')).json()['settings']==settings
        bad={**settings,'draft_templates':[{**settings['draft_templates'][0],'attention':'{product.__class__}'}]}
        assert (await client.put('/api/studio/workspace',json=bad)).status_code==422
        brief={'name':'Backend campaign','objective':'Measure real campaign results','info_bank':'Actual product information',
               'audience':'Opted-in customers','audience_size':10,'product_name':'Actual product','value_proposition':'A useful actual benefit',
               'proof':'Verified proof','offer':'Saved offer','call_to_action':'Visit the product page','primary_metric':'conversions',
               'target_value':2,'deadline':(date.today()+timedelta(days=7)).isoformat(),'consent_confirmed':True}
        response=await client.post('/api/campaigns',json=brief);assert response.status_code==200,response.text
        cid=response.json()['id']
        detail=(await client.get(f'/api/campaigns/{cid}')).json()
        assert detail['currency']=='JPY' and detail['category']=='custom category' and detail['language']=='Japanese' and detail['tone']=='brief'
        drafts=(await client.post(f'/api/campaigns/{cid}/drafts')).json()['variants'];assert len(drafts)==3
        assert drafts[0]['attention']=='Actual product: A useful actual benefit' and drafts[0]['score_out_of']==4
        assert 'STOP' in drafts[0]['action']
        with patch.object(studio,'telegram',AsyncMock(return_value={'type':'private','first_name':'Real contact'})):
            denied=await client.post(f'/api/studio/campaigns/{cid}/audience',json={'chat_id':123,'consent_confirmed':False})
            assert denied.status_code==422
            response=await client.post(f'/api/studio/campaigns/{cid}/audience',json={'chat_id':123,'consent_confirmed':True})
            assert response.status_code==200,response.text
            contact_id=response.json()['id']
        with patch.object(studio,'sync_updates',AsyncMock(return_value=0)):
            response=await client.post(f'/api/studio/campaigns/{cid}/launch',json={'variant_id':drafts[0]['id'],'confirmed':True})
            assert response.status_code==200,response.text
            job=response.json()['id']
            for action in ['pause','resume']:
                assert (await client.post(f'/api/studio/broadcasts/{job}/{action}')).status_code==200
            async def wait_delivery():
                while True:
                    result=(await client.get(f'/api/studio/campaigns/{cid}/deliveries')).json()
                    if result[0]['counts'].get('sent')==1:return
                    await asyncio.sleep(.02)
            with patch.object(studio,'telegram',AsyncMock(return_value={'message_id':77})):
                task=asyncio.create_task(studio.worker())
                try:await asyncio.wait_for(wait_delivery(),5)
                finally:
                    task.cancel()
                    with suppress(asyncio.CancelledError):await task
                assert (await client.post(f'/api/studio/audience/{contact_id}/reply',json={'text':'Owner reply'})).status_code==200
        assert (await client.get('/api/studio/inbox')).json()[0]['text']=='Owner reply'
        today=date.today().isoformat()
        metrics={'variant_id':drafts[0]['id'],'period':today,'delivered':10,'clicks':3,'conversions':1,'replies':1,'signups':1,'opt_outs':1,'complaints':0,'spend':100}
        assert (await client.post(f'/api/campaigns/{cid}/metrics',json=metrics)).status_code==200
        recommendations=(await client.get(f'/api/campaigns/{cid}/optimization')).json()
        assert '10%' in recommendations['benchmark_note'] and '1-delivered' in recommendations['benchmark_note']
        response=await client.post('/api/studio/conversions',json={'campaign_id':cid,'name':'Saved sale','amount':250,'occurred_on':today})
        assert response.status_code==200,response.text
        assert response.json()['currency']=='JPY' and response.json()['source']=='owned-site'
        analytics=(await client.get('/api/studio/analytics')).json()
        assert analytics['currency']=='JPY' and analytics['finance']['roas']==2.5 and analytics['live']['sent']==1
        assert (date.fromisoformat(analytics['end'])-date.fromisoformat(analytics['start'])).days==6
        report=(await client.get('/api/studio/reports')).json();assert report['analytics']==analytics
        for kind in ['summary','daily','campaigns','conversions']:
            assert (await client.get('/api/studio/report-export',params={'kind':kind})).status_code==200
        for kind in ['contacts','metrics','deliveries']:
            assert (await client.get(f'/api/studio/campaigns/{cid}/export',params={'kind':kind})).status_code==200
        with patch.object(connection,'bot_call',AsyncMock(return_value={'status':'sent','result':{'message_id':99}})):
            token='999999:'+'b'*30
            response=await client.post('/api/studio/direct-send',json={'bot_token':token,'chat_ids':['123'],'text':'Saved direct message','confirmed':True})
            assert response.status_code==200,response.text
        history=(await client.get('/api/studio/direct-history')).json()
        assert history[0]['id']==response.json()['id'] and history[0]['results'][0]['message_id']==99 and token not in str(history)
        async with async_session() as session:
            stored=await session.get(DirectSendRecord,history[0]['id']);assert stored.text=='Saved direct message'
            interrupted=DirectSendRecord(owner_id=0,bot_id='999',text='Interrupted',results=[{'chat_id':'1','status':'unknown'}, {'chat_id':'2','status':'pending'}])
            session.add(interrupted)
            session.add(Campaign(name='Other owner',telegram_user_id=9,status='running'));await session.commit()
            interrupted_id=interrupted.id
        await connection.recover_direct_history()
        async with async_session() as session:
            interrupted=await session.get(DirectSendRecord,interrupted_id)
            assert [r['status'] for r in interrupted.results]==['unknown','skipped']
        status=(await client.get('/api/status')).json();assert status['campaigns']==1 and status['active_campaigns']==1
        app.dependency_overrides[authenticated_user]=lambda:{'id':999}
        for path in ['/api/status','/api/studio/workspace','/api/studio/direct-history']:
            assert (await client.get(path)).status_code==403
        app.dependency_overrides.clear()
    await engine.dispose()
    print('PASS: saved workspace defaults and templates, campaigns, audience consent, queued delivery worker, replies, metrics, conversion defaults, analytics, report/CSV, durable direct history, real status counts and authorization')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='campaign-backend-') as temporary:
        asyncio.run(check(Path(temporary)))
