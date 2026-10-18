"""Reports integration checks with an isolated database and no Telegram calls."""
import asyncio
import csv
from datetime import datetime
import io
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def check(directory):
    os.environ.update(APP_ENV='development', TELEGRAM_BOT_TOKEN='', CHAT_ID='12345',
                      DATABASE_URL=f'sqlite+aiosqlite:///{directory.as_posix()}/reports.db')
    import httpx
    from sqlalchemy import select, func
    from app.main import app
    from app.database import init_db, async_session, engine
    from app.models import Campaign, CampaignVariant, MetricSnapshot, Conversion, SavedReport, Broadcast, Delivery, Subscriber
    from app.telegram_auth import authenticated_user
    await init_db()
    await init_db()
    async with async_session() as s:
        campaign=Campaign(name='=SUM(1,2)',objective='Reporting',currency='INR',budget=500,telegram_user_id=0)
        foreign=Campaign(name='Private campaign',telegram_user_id=9,currency='USD')
        s.add_all([campaign,foreign]);await s.flush()
        variant=CampaignVariant(campaign_id=campaign.id,label='A');s.add(variant);await s.flush()
        s.add(MetricSnapshot(campaign_id=campaign.id,variant_id=variant.id,period='2026-10-18',delivered=100,clicks=10,conversions=7,spend=50))
        subscriber=Subscriber(campaign_id=campaign.id,chat_id=123,consent_source='owner');s.add(subscriber);await s.flush()
        job=Broadcast(campaign_id=campaign.id,variant_id=variant.id,text='Hello',scheduled_at=datetime(2026,10,18),status='completed');s.add(job);await s.flush()
        s.add_all([Delivery(broadcast_id=job.id,subscriber_id=subscriber.id,status='sent',sent_at=datetime(2026,10,18))])
        for i in range(15):
            s.add(Conversion(campaign_id=campaign.id,variant_id=variant.id,name='=danger\nquoted,"text"' if i==0 else f'Event {i}',
                             amount=20 if i<10 else 30,currency='INR' if i<10 else 'USD',source='telegram',
                             status='confirmed' if i<12 else ['pending','refunded','cancelled'][i-12],occurred_on='2026-10-18'))
        s.add(Conversion(campaign_id=campaign.id,name='Old',amount=900,currency='INR',status='confirmed',occurred_on='2025-01-01'))
        s.add(Conversion(campaign_id=foreign.id,name='Hidden',amount=999,currency='INR',status='confirmed',occurred_on='2026-10-18'))
        await s.commit();cid=campaign.id;foreign_id=foreign.id
    params={'start':'2026-10-18','end':'2026-10-18','currency':'INR'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/api/studio/reports',params=params)
        assert response.status_code==200,response.text
        report=response.json();d=report['analytics']
        assert d['live']['sent']==1 and d['manual']['conversions']==7
        assert d['ledger']['confirmed']==12 and d['ledger']['revenue']==200 and d['finance']['spend']==50 and d['finance']['roas']==4
        assert report['conversions']['total']==15 and len(report['conversions']['items'])==10
        assert report['conversions']['summary']['USD']['revenue']==60
        same=(await client.get('/api/studio/reports',params=params)).json()
        assert same['id']==report['id'] and same['saved_at']==report['saved_at']
        async with async_session() as s:
            assert await s.scalar(select(func.count()).select_from(SavedReport))==1
        analytics=(await client.get('/api/studio/analytics',params=params)).json()
        assert analytics==d
        for kind in ['summary','daily','campaigns','conversions']:
            response=await client.get('/api/studio/report-export',params={**params,'kind':kind})
            assert response.status_code==200,response.text
            rows=list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff'))))
            assert rows and 'attachment' in response.headers['content-disposition']
            if kind=='conversions':
                assert len(rows)==15 and any(r['name'].startswith("'=danger\n") for r in rows)
            if kind=='campaigns':assert rows[0]['name'].startswith("'=SUM")
            if kind=='daily':assert rows[0]['recorded_conversions']=='12' and rows[0]['manual_conversions']=='7'
        usd=(await client.get('/api/studio/reports',params={**params,'currency':'USD'})).json()['analytics']
        assert usd['ledger']['revenue']==60 and usd['finance']['spend']==0 and usd['finance']['roas'] is None
        item=(await client.get('/api/studio/conversions',params=params)).json()['items'][-1]
        values={key:item[key] for key in ['campaign_id','variant_id','subscriber_id','name','kind','amount','currency','source','reference','occurred_on','notes']}
        values['status']='refunded'
        assert (await client.put('/api/studio/conversions/'+str(item['id']),json=values)).status_code==200
        after=(await client.get('/api/studio/reports',params=params)).json()
        assert after['id']==report['id'] and after['analytics']['ledger']['confirmed']==11
        assert after['saved_at']!=report['saved_at']
        for path in ['/api/studio/reports','/api/studio/report-export']:
            assert (await client.get(path,params={**params,'campaign_id':foreign_id})).status_code==404
            assert (await client.get(path,params={**params,'start':'2026-10-19'})).status_code==422
            assert (await client.get(path,params={**params,'start':'2020-01-01'})).status_code==422
        empty=(await client.get('/api/studio/reports',params={**params,'start':'2026-10-17','end':'2026-10-17'})).json()
        assert empty['analytics']['ledger']['confirmed']==0 and empty['conversions']['total']==0
        app.dependency_overrides[authenticated_user]=lambda:{'id':999}
        assert (await client.get('/api/studio/reports',params=params)).status_code==403
        app.dependency_overrides.clear()
    await engine.dispose()
    print('PASS: report/analytics equality, automatic snapshot persistence and updates, no duplicates, currencies, conversion edits, full CSV exports, CSV escaping, filters, empty states and authorization')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='campaign-reports-') as temporary:
        asyncio.run(check(Path(temporary)))
