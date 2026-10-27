"""Pure analysis, calendar, and durable storage; no GUI or network dependencies."""
from __future__ import annotations
import csv
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from collections import deque
from decimal import Decimal

SH = timezone(timedelta(hours=8))
# Exchange holiday ranges, not compensatory working weekends. Unknown years fail closed.
HOLIDAYS_2026 = [('01-01','01-03'), ('02-15','02-23'), ('04-04','04-06'),
                 ('05-01','05-05'), ('06-19','06-21'), ('09-25','09-27'), ('10-01','10-07')]

def now():
    return datetime.now(SH)

def session(ts: datetime, force=False, holidays=None):
    ts = ts.astimezone(SH)
    if force:
        return '测试监控'
    if holidays is None and ts.year != 2026:
        return '日历未配置：请导入当年休市日'
    off = holidays if holidays is not None else {
        f'2026-{(date.fromisoformat("2026-"+a)+timedelta(days=n)).strftime("%m-%d")}'
        for a,b in HOLIDAYS_2026
        for n in range((date.fromisoformat('2026-'+b)-date.fromisoformat('2026-'+a)).days+1)
    }
    if ts.weekday() >= 5 or ts.date().isoformat() in off:
        return '休市日'
    t = ts.time().replace(tzinfo=None)
    if time(9,15) <= t < time(9,25):
        return '集合竞价'
    if time(9,30) <= t < time(11,30) or time(13) <= t < time(15):
        return '连续交易'
    return '非交易时段'

def active(s):
    return s in ('连续交易', '集合竞价', '测试监控')

def number(text):
    """Reject ambiguous OCR instead of guessing a decimal or a missing sign."""
    text = str(text).strip().replace('−','-').replace('，', ',').replace('％','%')
    m = re.fullmatch(r'([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(亿|万)?(?:%|手|元)?', text)
    if not m:
        return None
    value = float(Decimal(m[1].replace(',', '')) * {'亿':100000000,'万':10000,None:1}[m[2]])
    return value if math.isfinite(value) else None

@dataclass
class Tick:
    ts: str
    code: str
    values: dict
    confidence: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)

    @property
    def dt(self):
        return datetime.fromisoformat(self.ts).astimezone(SH)

class Sanity:
    def __init__(self):
        self.previous = None

    def check(self, tick):
        v = dict(tick.values)
        old = self.previous
        for key, val in list(v.items()):
            if val is None:
                continue
            bad = not isinstance(val, (int,float)) or not math.isfinite(val)
            bad |= tick.confidence.get(key,1) < .75
            if key in ('price','high','low','open','limit_up','limit_down','avg_price'):
                bad |= val <= 0
            if key in ('volume_total','amount_total','turnover_rate','outer_vol','inner_vol'):
                bad |= val < 0
            if key == 'turnover_rate':
                bad |= val > 1000
            if old and old.code == tick.code and old.dt.date() == tick.dt.date():
                prev = old.values.get(key)
                gap = (tick.dt-old.dt).total_seconds()
                if prev is not None and 0 <= gap < 60:
                    if key == 'price':
                        bad |= abs(val/prev-1) > .03
                    if key in ('volume_total','amount_total','turnover_rate'):
                        bad |= val < prev
            if bad:
                v[key] = None
                tick.flags.append('invalid:'+key)
        if v.get('price') is not None:
            if v.get('limit_up') is not None and v['price'] > v['limit_up']+.011:
                v['price'] = None
                tick.flags.append('above_limit')
            if v.get('limit_down') is not None and v['price'] < v['limit_down']-.011:
                v['price'] = None
                tick.flags.append('below_limit')
        tick.values = v
        if v.get('price') is not None:
            # Preserve valid cumulative baselines across intermittent missing fields.
            if old and old.code == tick.code and old.dt.date() == tick.dt.date():
                baseline = dict(old.values)
                baseline.update({k:x for k,x in v.items() if x is not None})
                self.previous = Tick(tick.ts,tick.code,baseline)
            else:
                self.previous = tick
        return tick

@dataclass
class Rule:
    id: str
    name: str
    severity: str
    conditions: list
    explanation: str
    source: str = '量价技术分析启发式；非书籍原文、非统计概率'

# Conditions operate only on available measured features. No eval and no inferred fund intent.
RULES = [
 Rule('healthy_uptrend','温和放量上涨','info', [('pct_from_avg','>',0),('pct_1m','>',0),('vol_ratio_1m','>=',1.2),('vol_ratio_1m','<=',2.5),('outer_ratio','>',.55),('main_flow_delta','>=',0)],'价在均价上方，温和放量，主动成交与资金读数支持'),
 Rule('strong_pullup_dump','疑似拉升后派发','danger',[('rise_3m','>',2),('drawdown','<',-.6),('vol_ratio_1m','>',3),('range_pos','<',.7),('main_flow_delta','<',0)],'短时拉升后回落、放量且资金读数流出；不能据此确认主力意图'),
 Rule('fake_breakout','疑似假突破','warn',[('failed_breakout','==',1),('vol_ratio_1m','<',1.2)],'突破前期高点后回落且量能未持续放大'),
 Rule('volume_price_divergence_up','上涨量能背离','warn',[('new_high','==',1),('volume_falling','==',1)],'价格新高而连续三分钟成交增量递减'),
 Rule('volume_price_divergence_down','下跌抛压减弱迹象','info',[('new_low','==',1),('vol_ratio_1m','<',.7)],'价格新低但成交增量低于近期均值'),
 Rule('wash_out','缩量回撤，待确认','info',[('pct_1m','<',0),('vol_ratio_1m','<',.7),('net_big_order','>=',0),('pct_from_avg','>',-3)],'缩量回撤且大单读数未净流出；洗盘仅是待验证假设'),
 Rule('slow_bleed','缩量阴跌','warn',[('down_streak','>=',5),('vol_ratio_1m','<',1),('range_pos','<',.3)],'连续五分钟下跌，量能偏低且处日内低位'),
 Rule('panic_dump','放量急跌','danger',[('pct_1m','<',-2),('vol_ratio_1m','>',4),('ob_imbalance','<',-.4)],'快速下跌、成交激增、卖方挂单占优'),
 Rule('limit_up_sealed','涨停封单可见','info',[('at_limit','==',1),('bid_volume','>',10000)],'价格接近识别到的涨停价，买盘封单较大'),
 Rule('limit_up_opening','涨停打开','danger',[('opened_limit','==',1),('vol_ratio_1m','>',1.5)],'前一分钟接近涨停，本分钟打开并放量'),
 Rule('limit_down_risk','接近跌停','danger',[('down_gap','<',.5),('outer_ratio','<',.3)],'距离识别到的跌停价不足0.5%，主动卖出读数占优'),
 Rule('order_book_trap','挂单与成交背离','warn',[('wei_bi','>',50),('outer_ratio','<',.45),('abs_pct_1m','<',.1)],'委比较高但成交方向及价格未印证；挂单可随时撤销'),
 Rule('big_sell_wall','上方挂单压力','warn',[('ask_volume','>',20000),('pct_1m','<=',0)],'卖档挂单较大且价格未上行；不等于真实抛售'),
 Rule('big_buy_support','买盘支撑迹象','info',[('bid_volume','>',20000),('pct_1m','>=',0),('main_flow_delta','>',0)],'买档挂单较大，价格企稳且资金读数增加'),
 Rule('turnover_overheat','高位换手活跃','warn',[('turnover_rate','>',20),('range_pos','>',.8)],'日内高位且累计换手超过20%，筹码交换活跃'),
 Rule('turnover_dead','交投清淡','info',[('turnover_rate','<',1),('amplitude','<',1.5)],'累计换手和日内振幅均较低'),
 Rule('auction_strong','竞价偏强','info',[('auction','==',1),('pct_change','>',2),('auction_ratio_5d','>',1.5)],'竞价涨幅和相对前五日同期量均偏强'),
 Rule('auction_fake','竞价量价异常','warn',[('auction','==',1),('auction_cancel_ratio','>',.5),('pct_change','>',2)],'需逐笔委托历史才能验证；不以累计成交量代替撤单数据'),
 Rule('close_pump','尾盘拉升与资金背离','warn',[('late','==',1),('pct_3m','>',1.5),('vol_ratio_1m','>',2),('main_flow_delta','<',0)],'尾盘放量拉升而资金读数流出'),
 Rule('close_dump','尾盘放量下跌','danger',[('late','==',1),('pct_3m','<',-1.5),('vol_ratio_1m','>',2)],'尾盘三分钟明显下跌且放量'),
 Rule('vs_market_weak','弱于大盘','warn',[('vs_index','<',-1.5)],'个股涨幅低于所配置指数1.5个百分点以上'),
 Rule('vs_market_strong','相对大盘强势','info',[('vs_index','>',2),('vol_ratio_1m','>',1.2)],'相对指数较强且量能放大'),
 Rule('macd_minute_divergence','分时动能转弱','warn',[('macd_cross_down','==',1)],'MACD由正转负且DIFF下穿DEA'),
 Rule('abnormal_news','出现异动信息','warn',[('abnormal_news','==',1)],'需复核公告时间、全文及对应股票，标题本身不是方向信号'),
]

def match(conditions, features):
    ops = {'>':lambda a,b:a>b, '>=':lambda a,b:a>=b, '<':lambda a,b:a<b,
           '<=':lambda a,b:a<=b, '==':lambda a,b:a==b}
    return all(k in features and features[k] is not None and op in ops and
               ops[op](features[k], threshold) for k,op,threshold in conditions)

class Engine:
    def __init__(self, rule_config=None):
        self.rule_config = rule_config or {}

    def analyze(self, features):
        hits, unavailable = [], []
        for r in RULES:
            opts = self.rule_config.get(r.id,{})
            if not opts.get('enabled', True):
                continue
            conditions = [(k,op,opts.get('thresholds',{}).get(k+' '+op,v)) for k,op,v in r.conditions]
            if any(features.get(k) is None for k,_,_ in conditions):
                unavailable.append(r.id)
            elif match(conditions,features):
                hits.append(r)
        hits.sort(key=lambda r: {'danger':0,'warn':1,'info':2}[r.severity])
        if hits and hits[0].severity == 'danger':
            hits = [r for r in hits if r.id not in ('healthy_uptrend','wash_out','big_buy_support')]
        return {'state':' / '.join(r.name for r in hits[:2]) or '暂未出现明确量价信号',
                'severity':hits[0].severity if hits else 'neutral',
                'text':'；'.join(r.explanation for r in hits[:2]) or '数据积累中或已观测指标未触发规则，不代表走势安全。',
                'rule_ids':[r.id for r in hits], 'unavailable_rules':unavailable,
                'evidence':{k:round(v,5) for k,v in features.items() if isinstance(v,(int,float))},
                'source':'rule', 'confidence':None}

class Minutes:
    """Close completed minutes only, never duplicate, never bridge lunch or missing minutes."""
    def __init__(self, engine=None):
        self.engine = engine or Engine()
        self.bucket = []
        self.key = None
        self.history = deque(maxlen=60)
        self.last_closed = None
        self.code = None

    def add(self, tick):
        if tick.values.get('price') is None:
            return None
        if self.code is not None and self.code != tick.code:
            self.bucket = []; self.key = None; self.history.clear(); self.last_closed = None
        self.code = tick.code
        key = tick.dt.replace(second=0,microsecond=0).isoformat()
        if self.key is not None and key < self.key:
            return None
        result = None
        if self.key and key != self.key:
            result = self.close()
        self.key = key
        self.bucket.append(tick)
        return result

    def close(self):
        if not self.bucket or self.key == self.last_closed:
            return None
        ticks = self.bucket
        self.bucket = []
        self.last_closed = self.key
        last = ticks[-1]
        v = dict(last.values)
        price = v['price']
        dt = last.dt.replace(second=0,microsecond=0)
        if self.history and (dt-self.history[-1]['dt']).total_seconds() != 60:
            self.history.clear()
        h = list(self.history)
        f = {k:x for k,x in v.items() if x is not None}
        f['late'] = int(dt.hour == 14 and dt.minute >= 45)
        f['auction'] = int(session(dt) == '集合竞价')
        if v.get('avg_price'):
            f['pct_from_avg'] = (price/v['avg_price']-1)*100
        elif v.get('amount_total') is not None and v.get('volume_total',0):
            # Total volume is measured in hands (100 shares), amount in yuan.
            avg = v['amount_total'] / (v['volume_total']*100)
            if avg > 0:
                f['pct_from_avg'] = (price/avg-1)*100
        if v.get('high') is not None and v.get('low') is not None and v['high'] > v['low']:
            f['range_pos'] = (price-v['low'])/(v['high']-v['low'])
        if v.get('outer_vol') is not None and v.get('inner_vol') is not None:
            total = v['outer_vol']+v['inner_vol']
            if total > 0:
                f['outer_ratio'] = v['outer_vol']/total
        for key in ('bid_volume','ask_volume'):
            if v.get(key) is not None:
                f[key] = v[key]
        if v.get('bid_volume') is not None and v.get('ask_volume') is not None:
            total = v['bid_volume']+v['ask_volume']
            if total:
                f['ob_imbalance'] = (v['bid_volume']-v['ask_volume'])/total
        if v.get('net_xlarge') is not None and v.get('net_large') is not None:
            f['net_big_order'] = v['net_xlarge']+v['net_large']
        if v.get('limit_up'):
            f['at_limit'] = int(abs(price-v['limit_up']) < .011)
        if v.get('limit_down'):
            f['down_gap'] = (price/v['limit_down']-1)*100
        if v.get('pct_change') is not None and v.get('index_pct') is not None:
            f['vs_index'] = v['pct_change']-v['index_pct']
        vol = None
        if h:
            p = h[-1]
            f['pct_1m'] = (price/p['price']-1)*100
            f['abs_pct_1m'] = abs(f['pct_1m'])
            for src,dst in [('volume_total','vol_1m'),('turnover_rate','turnover_delta'),('main_net','main_flow_delta')]:
                prev = p['values'].get(src)
                if prev is not None and v.get(src) is not None:
                    delta = v[src]-prev
                    if src == 'main_net' or delta >= 0:
                        f[dst] = delta
            vol = f.get('vol_1m')
            vols = [x['vol'] for x in h[-10:] if x['vol'] is not None]
            if len(vols) >= 3 and mean(vols)>0 and vol is not None:
                f['vol_ratio_1m'] = vol/mean(vols)
            f['new_high'] = int(price > max(x['price'] for x in h))
            f['new_low'] = int(price < min(x['price'] for x in h))
            f['opened_limit'] = int(p['features'].get('at_limit') == 1 and f.get('at_limit') == 0)
            streak = 0
            for x in reversed(h + [{'features': f}]):
                if x['features'].get('pct_1m',0) < 0:
                    streak += 1
                else:
                    break
            f['down_streak'] = streak
            if len(h) >= 3:
                f['pct_3m'] = (price/h[-3]['price']-1)*100
                f['rise_3m'] = (max([price]+[x['price'] for x in h[-3:]])/h[-3]['price']-1)*100
                f['drawdown'] = (price/max([price]+[x['price'] for x in h[-3:]])-1)*100
                recent = [x['vol'] for x in h[-2:]]+[vol]
                if all(x is not None for x in recent):
                    f['volume_falling'] = int(recent[0]>recent[1]>recent[2])
                if len(h) >= 5:
                    baseline = max(x['price'] for x in h[:-2])
                    f['failed_breakout'] = int(max(x['price'] for x in h[-2:])>baseline and price<baseline)
            if all(v.get(k) is not None and p['values'].get(k) is not None for k in ('macd','diff','dea')):
                f['macd_cross_down'] = int(p['values']['macd']>0>v['macd'] and v['diff']<v['dea'] and p['values']['diff']>=p['values']['dea'])
        self.history.append({'dt':dt,'price':price,'vol':vol,'values':v,'features':f})
        conclusion = self.engine.analyze(f)
        if f['auction']:
            # Continuous-session predicates must not classify auction ticks.
            auction_engine = Engine({r.id:{'enabled': r.id.startswith('auction_')} for r in RULES})
            conclusion = auction_engine.analyze(f)
        conclusion.update(ts=dt.isoformat(),minute=dt.strftime('%H:%M'),code=last.code,price=price,
                          sample_count=len(ticks),values=v)
        return conclusion

class Store:
    def __init__(self, root):
        self.root = Path(root)
        for p in ('logs','data/ticks','data/conclusions','captures','analysis','config'):
            (self.root/p).mkdir(parents=True,exist_ok=True)
        self.keys = set()
        # Preserve idempotency after an application restart.
        for p in (self.root/'data/conclusions').glob('*.jsonl'):
            for line in p.read_text(encoding='utf-8').splitlines():
                try:
                    c = json.loads(line)
                    self.keys.add((c['ts'],c['source'],c.get('code','')))
                except (ValueError,KeyError):
                    pass

    def event(self, text, ts=None):
        ts = ts or now()
        with (self.root/'logs'/f'{ts:%Y-%m-%d}.log').open('a',encoding='utf-8') as f:
            f.write(f'[{ts:%H:%M:%S}] {text}\n')
            f.flush()

    def tick(self, t):
        path = self.root/'data/ticks'/f'{t.dt:%Y-%m-%d}.csv'
        fresh = not path.exists() or not path.stat().st_size
        with path.open('a',newline='',encoding='utf-8') as f:
            w = csv.writer(f)
            if fresh:
                w.writerow(['ts','code','values_json','confidence_json','flags_json'])
            w.writerow([t.ts,t.code,json.dumps(t.values,ensure_ascii=False),json.dumps(t.confidence),json.dumps(t.flags)])
            f.flush()

    def conclusion(self, c):
        key = (c['ts'],c['source'],c.get('code',''))
        if key in self.keys:
            return False
        dt = datetime.fromisoformat(c['ts']).astimezone(SH)
        path = self.root/'data/conclusions'/f'{dt:%Y-%m-%d}.jsonl'
        # A power loss can leave a partial tail. Preserve it but separate the next record.
        needs_newline = False
        if path.exists() and path.stat().st_size:
            with path.open('rb') as tail:
                tail.seek(-1,2)
                needs_newline = tail.read(1) != b'\n'
        with path.open('a',encoding='utf-8') as f:
            if needs_newline:
                f.write('\n')
            f.write(json.dumps(c,ensure_ascii=False,allow_nan=False)+'\n')
            f.flush()
        self.keys.add(key)
        self.event(f"{c['severity'].upper()} {c['code']} {c['minute']} {c['state']} | {c['text']} [{c['source']}] | 依据={json.dumps(c['evidence'],ensure_ascii=False)}",dt)
        return True

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    tmp.replace(path)
