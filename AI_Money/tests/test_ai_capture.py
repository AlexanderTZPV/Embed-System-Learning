import json
from datetime import datetime
from PIL import Image
import pytest
from ths_watch import ai
from ths_watch.capture import privacy_image,crop
from ths_watch.core import Engine

def conclusion():
    c=Engine().analyze({'price':9.53})
    c.update(ts='2026-10-26T09:31:00+08:00',minute='09:31',code='000530',price=9.53)
    return c

def test_agent_stable_and_correlated(tmp_path):
    day=tmp_path/'2026-10-26';day.mkdir()
    p=day/'0931.json'
    p.write_text(json.dumps({'minute':'09:31','text':'缩量回撤，数据仍待确认。'}),encoding='utf-8')
    inbox=ai.AgentInbox();c=conclusion();known={c['ts']:c}
    assert inbox.poll(tmp_path,day.name,known)==[]
    got=inbox.poll(tmp_path,day.name,known)
    assert len(got)==1 and got[0]['source']=='agent'
    assert got[0]['evidence']==c['evidence']
    assert inbox.poll(tmp_path,day.name,known)==[]
    (day/'0932.txt').write_text('未对应本次监控的分钟',encoding='utf-8')
    inbox.poll(tmp_path,day.name,known)
    assert inbox.poll(tmp_path,day.name,known)==[]

@pytest.mark.parametrize('text',['建议买入','保证收益','加仓至全仓'])
def test_filter(text):
    with pytest.raises(ValueError):ai.clean(text)

def test_partial_agent_json(tmp_path):
    d=tmp_path/'2026-10-26';d.mkdir();p=d/'0931.json'
    p.write_text('{')
    inbox=ai.AgentInbox();c=conclusion();known={c['ts']:c}
    inbox.poll(tmp_path,d.name,known)
    assert inbox.poll(tmp_path,d.name,known)==[]
    p.write_text('{"text":"量能不足，待观察"}',encoding='utf-8')
    inbox.poll(tmp_path,d.name,known)
    assert len(inbox.poll(tmp_path,d.name,known))==1

def test_api_privacy_and_timeout(monkeypatch):
    calls=[]
    class Response:
        status_code=200
        def json(self):return {'choices':[{'message':{'content':'量能偏弱，数据不足。'}}]}
    def post(*a,**k):calls.append((a,k));return Response()
    monkeypatch.setattr(ai,'api_key',lambda:'test-not-a-real-key')
    monkeypatch.setattr(ai.requests,'post',post)
    cfg={'base_url':'https://api.openai.com/v1','model':'example','upload_image':False}
    assert ai.enhance(cfg,conclusion(),Image.new('RGB',(20,20)))=='量能偏弱，数据不足。'
    kwargs=calls[0][1]
    assert kwargs['timeout']==(3,8)
    assert kwargs['allow_redirects'] is False
    assert len(kwargs['json']['messages'][1]['content'])==1
    cfg['upload_image']=True
    ai.enhance(cfg,conclusion(),Image.new('RGB',(20,20)))
    assert len(calls[-1][1]['json']['messages'][1]['content'])==2
    cfg['base_url']='http://insecure'
    with pytest.raises(ValueError):ai.enhance(cfg,conclusion())

def test_privacy_whitelist_excludes_top_and_news():
    im=Image.new('RGB',(1920,1040),'red')
    safe=privacy_image(im)
    assert safe.size==(int(.853*1920)-int(.04*1920),int(.70*1040)-int(.075*1040))
    assert crop(im,[0,0,.5,.5]).size==(960,520)
