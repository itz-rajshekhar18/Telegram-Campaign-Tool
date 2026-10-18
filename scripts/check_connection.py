"""Isolated connection API checks; never sends Telegram messages or edits real .env."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def check(directory):
    os.environ.update(APP_ENV="development", TELEGRAM_BOT_TOKEN="123456:" + "a" * 30,
                      CHAT_ID="12345", DATABASE_URL=f"sqlite+aiosqlite:///{directory.as_posix()}/checks.db")
    import httpx
    from app.main import app
    from app.database import init_db, engine, async_session
    from app.models import Campaign
    from app import connection
    from app.telegram_auth import authenticated_user

    connection.ENV_PATH = directory / ".env"
    connection.ENV_PATH.write_text("# keep this\nOTHER_KEY='untouched'\nTELEGRAM_BOT_TOKEN='" + os.environ["TELEGRAM_BOT_TOKEN"] + "'\nCHAT_ID='12345'\n", encoding="utf-8")
    await init_db()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        response = await client.get('/api/studio/settings')
        assert response.status_code == 200 and token not in response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['restart_required'] is False
        with patch.object(connection, 'bot_call', AsyncMock(return_value={'status':'sent','result':{'username':'demo_bot'}})):
            response = await client.put('/api/studio/settings', json={'bot_token':'','owner_chat_id':'67890'})
        assert response.status_code == 200 and response.json()['restart_required']
        saved = connection.ENV_PATH.read_text(encoding='utf-8')
        assert "OTHER_KEY='untouched'" in saved and "CHAT_ID='67890'" in saved
        assert (await client.get('/api/studio/settings')).json()['restart_required']
        secret = 'malformed-secret-value'
        response = await client.put('/api/studio/settings', json={'bot_token':secret,'owner_chat_id':'1'})
        assert response.status_code == 422 and secret not in response.text
        with patch.object(connection, 'bot_call', AsyncMock(return_value={'status':'failed'})):
            response = await client.put('/api/studio/settings', json={'bot_token':token,'owner_chat_id':'1'})
        assert response.status_code == 422 and connection.ENV_PATH.read_text(encoding='utf-8') == saved
        payload = {'chat_ids':['12345','12345','-1001234567890','@my_channel'], 'text':'hello', 'confirmed':True}
        mock = AsyncMock(side_effect=[{'status':'sent','result':{'message_id':7}}, {'status':'failed','detail':'Forbidden','code':403}, {'status':'sent','result':{'message_id':8}}])
        with patch.object(connection, 'bot_call', mock), patch.object(connection.asyncio, 'sleep', AsyncMock()):
            response = await client.post('/api/studio/direct-send', json=payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['sent'] == 2 and len(result['results']) == 3 and mock.await_count == 3
        for data in [{**payload,'confirmed':False}, {**payload,'chat_ids':['0']}, {**payload,'text':'  '}, {**payload,'chat_ids':['1']*21}]:
            assert (await client.post('/api/studio/direct-send',json=data)).status_code == 422
        for outcome in [{'status':'unknown','detail':'Uncertain'}, {'status':'failed','detail':'Rate limit','retry_after':30}, {'status':'failed','detail':'Bad token','code':401}]:
            mock = AsyncMock(return_value=outcome)
            with patch.object(connection,'bot_call',mock):
                response = await client.post('/api/studio/direct-send',json=payload)
            assert mock.await_count == 1 and response.json()['results'][1]['status'] == 'skipped'
        async with connection.send_lock:
            assert (await client.post('/api/studio/direct-send',json=payload)).status_code == 409
        async with async_session() as session:
            session.add(Campaign(name='Existing', objective='Test', telegram_user_id=0))
            await session.commit()
        with patch.object(connection,'bot_call',AsyncMock(return_value={'status':'sent','result':{'username':'other'}})):
            response = await client.put('/api/studio/settings',json={'bot_token':'987654:'+'b'*30,'owner_chat_id':'12345'})
        assert response.status_code == 409
        app.dependency_overrides[authenticated_user] = lambda: {'id':99999}
        for method, path, body in [('GET','/api/studio/settings',None),('PUT','/api/studio/settings',{'owner_chat_id':'1'}),('POST','/api/studio/direct-send',payload)]:
            assert (await client.request(method,path,json=body)).status_code == 403
        app.dependency_overrides.clear()
        assert (await client.get('/')).status_code == 200
        assert (await client.get('/static/connection.js')).status_code == 200
    # Exercise transport failures and ensure remote descriptions cannot echo secrets.
    for fake_response in [httpx.Response(200,text='not json'), httpx.Response(400,json={'ok':False,'description':token})]:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value.post.return_value = fake_response
        with patch.object(connection.httpx,'AsyncClient',return_value=mock_client):
            outcome = await connection.bot_call(token,'sendMessage',{})
        assert token not in str(outcome) and outcome['status'] in {'unknown','failed'}
    await engine.dispose()
    print('PASS: isolated settings, secret redaction, authorization, deduplication, partial sends, rate limits, uncertain delivery, concurrency and bot ownership checks')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='campaign-connection-') as temporary:
        asyncio.run(check(Path(temporary)))
