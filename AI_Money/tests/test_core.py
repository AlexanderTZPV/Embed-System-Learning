from datetime import datetime,timedelta
import json
import pytest
from ths_watch.core import Tick,Sanity,session,SH,number,RULES,Engine,match,Minutes,Store
from ths_watch.worker import demo_tick
from ths_watch.replay import replay

def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=SH)

@pytest.mark.parametrize('stamp,expected',[
 ('2026-10-26T09:14:59','非交易时段'),('2026-10-26T09:15:00','集合竞价'),
 ('2026-10-26T09:25:00','非交易时段'),('2026-10-26T09:30:00','连续交易'),
 ('2026-10-26T11:29:59','连续交易'),('2026-10-26T11:30:00','非交易时段'),
 ('2026-10-26T13:00:00','连续交易'),('2026-10-26T15:00:00','非交易时段'),
 ('2026-10-01T10:00:00','休市日'),('2026-02-23T10:00:00','休市日'),
 ('2026-10-24T10:00:00','休市日'),('2027-01-04T10:00:00','日历未配置：请导入当年休市日')])
def test_session(stamp,expected):
    assert session(dt(stamp)) == expected

def test_timezone():
    assert session(datetime.fromisoformat('2026-10-26T02:00:00+00:00'))=='连续交易'

@pytest.mark.parametrize('text,value',[('231.6万',2316000),('21.10亿',2110000000),('38.59%',38.59),('-8,222.4',-8222.4),('9.53',9.53),('9.53 9.55',None),('O.92',None),('nan',None),('最新9.53',None)])
def test_number(text,value):
    assert number(text)==value

def test_sanity():
    s=Sanity()
    s.check(Tick('2026-10-26T10:00:00+08:00','000530',{'price':9.53,'volume_total':100,'turnover_rate':10}))
    bad=s.check(Tick('2026-10-26T10:00:03+08:00','000530',{'price':95.3,'volume_total':90,'turnover_rate':9}))
    assert all(x is None for x in bad.values.values())
    assert len(bad.flags)==3
    ok=s.check(Tick('2026-10-26T10:00:06+08:00','000530',{'price':9.54,'volume_total':110,'turnover_rate':10.1}))
    assert ok.values['price']==9.54
    missing=s.check(Tick('2026-10-26T10:00:09+08:00','000530',{'price':9.55,'volume_total':None}))
    lower=s.check(Tick('2026-10-26T10:00:12+08:00','000530',{'price':9.55,'volume_total':105}))
    assert lower.values['volume_total'] is None
    nxt=s.check(Tick('2026-10-27T09:30:00+08:00','000530',{'price':9.8,'volume_total':5,'turnover_rate':.1}))
    assert nxt.values['volume_total']==5

@pytest.mark.parametrize('rule',RULES,ids=lambda r:r.id)
def test_rules_hit_and_missing(rule):
    features={}
    for k,op,v in rule.conditions:
        # Choose an interior point of the lower/upper interval.
        features[k] = v+(.01 if op=='>' else 0) if op in ('>','>=','==') else v-(.01 if op=='<' else 0)
    # bounded interval in healthy rule
    if rule.id=='healthy_uptrend':features['vol_ratio_1m']=1.5
    assert match(rule.conditions,features)
    assert rule.id in Engine().analyze(features)['rule_ids']
    features.pop(rule.conditions[0][0])
    assert not match(rule.conditions,features)
    assert rule.id not in Engine().analyze(features)['rule_ids']

def test_unknown_not_zero_and_threshold_override():
    c=Engine().analyze({'price':9.53})
    assert c['rule_ids']==[] and len(c['unavailable_rules'])==24
    c=Engine({'turnover_overheat':{'thresholds':{'turnover_rate >':40}}}).analyze({'turnover_rate':30,'range_pos':.9})
    assert 'turnover_overheat' not in c['rule_ids']

def test_minutes_store_replay(tmp_path):
    m=Minutes();store=Store(tmp_path/'live');results=[]
    for i in range(12):
        t,_=demo_tick(i,dt('2026-10-26T09:30:00')+timedelta(minutes=i),'000530')
        store.tick(t)
        c=m.add(t)
        if c:
            assert store.conclusion(c)
            assert not store.conclusion(c)
            results.append(c)
    assert len(results)==11
    danger=[c for c in results if 'strong_pullup_dump' in c['rule_ids']]
    assert danger and danger[0]['severity']=='danger'
    assert 'healthy_uptrend' not in danger[0]['rule_ids']
    again=Store(tmp_path/'live')
    assert not again.conclusion(results[0])
    csv=tmp_path/'live/data/ticks/2026-10-26.csv'
    assert replay(csv,tmp_path/'replay')==11
    lines=(tmp_path/'live/data/conclusions/2026-10-26.jsonl').read_text().splitlines()
    assert len(lines)==11
    assert len((tmp_path/'live/logs/2026-10-26.log').read_text().splitlines())==11
    assert json.loads(lines[8])['evidence']['vol_ratio_1m']>3

def test_no_bridge_over_lunch_or_missing_minute():
    m=Minutes()
    for stamp in ['2026-10-26T11:28:00','2026-10-26T11:29:00','2026-10-26T13:00:00','2026-10-26T13:01:00']:
        c=m.add(Tick(dt(stamp).isoformat(),'000530',{'price':9.5,'volume_total':100}))
    assert c['minute']=='13:00'
    assert 'pct_1m' not in c['evidence']

def test_daily_rollover(tmp_path):
    store=Store(tmp_path)
    for stamp in ['2026-10-30T23:59:00','2026-11-01T00:00:00']:
        store.event('test',dt(stamp))
    assert len(list((tmp_path/'logs').glob('*.log')))==2

def test_partial_jsonl_tail_does_not_merge_next_record(tmp_path):
    store=Store(tmp_path)
    path=tmp_path/'data/conclusions/2026-10-26.jsonl'
    path.write_text('{"partial":',encoding='utf-8')
    c=Engine().analyze({})
    c.update(ts='2026-10-26T09:31:00+08:00',minute='09:31',code='000530')
    assert store.conclusion(c)
    assert json.loads(path.read_text().splitlines()[-1])['code']=='000530'
    assert not Store(tmp_path).conclusion(c)
    other=dict(c,code='600000')
    assert store.conclusion(other)
