from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import json
import threading
import time
import shutil
from PIL import Image, ImageDraw
from PySide6.QtCore import QThread, Signal
from . import ai
from .core import Tick, Store, Sanity, Minutes, Engine, now, session, active, atomic_json, SH
from .capture import Reader, locate, grab, privacy_image, CaptureError


def demo_tick(index, ts, code):
    # Explicitly simulated, never read from the user's market or presented as a real quote.
    prices = [9.00,9.01,9.02,9.03,9.04,9.05,9.32,9.45,9.25,9.18,9.12,9.11]
    price = prices[index % len(prices)]
    cycle = index//len(prices)
    vols = [1000,1100,1000,1050,1200,1300,5000,6000,18000,9000,6000,2500]
    total = cycle*sum(vols)+sum(vols[:index%len(prices)+1])
    v = {'price':price,'volume_total':100000+total,'amount_total':(100000+total)*900,
         'turnover_rate':3+total/100000,'high':9.46,'low':8.9,'amplitude':6.2,
         'outer_vol':55000+total*.6,'inner_vol':45000+total*.4,'main_net':1000-index*150,
         'pct_change':(price/9-1)*100,'wei_bi':65,'bid_volume':5000,'ask_volume':25000,
         'limit_up':9.9,'limit_down':8.1}
    im = Image.new('RGB',(1000,550),'#101820')
    d = ImageDraw.Draw(im)
    d.text((20,20),'SIMULATED DEMO - NOT LIVE MARKET DATA',fill='#fbbf24')
    points = [(30+i*70,400-(p-9)*550) for i,p in enumerate(prices[:index%len(prices)+1])]
    if len(points)>1:
        d.line(points,fill='white',width=3)
    d.text((20,480),f'{ts.isoformat()} code={code} price={price} total_volume={v["volume_total"]}',fill='white')
    return Tick(ts.isoformat(),code,v),im

class Worker(QThread):
    status = Signal(str)
    ticked = Signal(object)
    concluded = Signal(object)

    def __init__(self, config, root):
        super().__init__()
        self.config = dict(config)
        self.root = Path(root)/('demo' if config.get('demo') else '')
        self.halt = threading.Event()
        self.known = {}
        self.last_error = None
        self.last_error_time = 0
        self.minute_image = None
        self.future = None
        self.llm_errors = 0
        self.executor = None
        self.capture_root = Path(config['capture_directory']) if config.get('capture_directory') and not config.get('demo') else self.root/'captures'
        self.analysis_root = Path(config['analysis_directory']) if config.get('analysis_directory') and not config.get('demo') else self.root/'analysis'

    def stop(self):
        self.halt.set()

    def report_error(self, msg):
        self.status.emit(msg)
        if msg != self.last_error or time.monotonic()-self.last_error_time > 60:
            self.store.event('WARN '+msg)
            self.last_error,self.last_error_time = msg,time.monotonic()

    def publish(self,c,image):
        if self.config.get('demo'):
            c['simulated'] = True
            c['state'] = '【演示】'+c['state']
        dt = datetime.fromisoformat(c['ts'])
        folder = self.capture_root/c['code']/f'{dt:%Y-%m-%d}'
        folder.mkdir(parents=True,exist_ok=True)
        if image is not None and shutil.disk_usage(self.root).free > 500*1024*1024:
            path = folder/f'{dt:%H%M}.png'
            temp = path.with_suffix('.tmp')
            image.save(temp,format='PNG')
            temp.replace(path)
            c['snapshot_ref'] = str(path)
        else:
            c['snapshot_ref'] = None
            self.report_error('截图未保存：图像缺失或磁盘剩余不足500MB')
        atomic_json(folder/f'{dt:%H%M}.json',c)
        self.store.conclusion(c)
        self.known[c['ts']] = c
        self.concluded.emit(c)
        if self.config.get('llm_enabled') and not self.config.get('demo') and self.llm_errors<3:
            if self.future is None:
                self.future = (self.executor.submit(ai.enhance,self.config,c,image),c)
            else:
                self.store.event('WARN 上一条AI请求未完成，本分钟保留本地结论')

    def poll(self):
        if self.future and self.future[0].done():
            job, original = self.future
            self.future = None
            try:
                c = dict(original)
                c.update(text=job.result(),source='llm',state='AI辅助解读 · '+original['state'])
                self.llm_errors = 0
                if self.store.conclusion(c):
                    self.concluded.emit(c)
            except Exception:
                self.llm_errors += 1
                self.report_error(f'AI请求失败（{self.llm_errors}/3），使用本地结论；请检查API设置/余额/网络')
        if self.config.get('external_agent'):
            for c in self.inbox.poll(self.analysis_root/self.config['code'],now().date().isoformat(),self.known):
                if self.store.conclusion(c):
                    self.concluded.emit(c)

    def run(self):
        try:
            self.store = Store(self.root)
            self.store.event('INFO 开始监控'+(' [SIMULATED DEMO]' if self.config.get('demo') else ''))
            self.inbox = ai.AgentInbox()
            self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='AI')
            self.loop()
        except Exception as e:
            # No exception strings from API calls; these may embed request details.
            self.status.emit('监控已停止：'+type(e).__name__+'；请检查磁盘、配置和依赖')
            if hasattr(self,'store'):
                try:
                    self.store.event('ERROR 监控中止 '+type(e).__name__)
                except OSError:
                    pass
        finally:
            if self.executor:
                self.executor.shutdown(wait=False,cancel_futures=True)

    def loop(self):
        minutes = Minutes(Engine(self.config.get('rules')))
        sanity = Sanity()
        reader = None
        last_sample = 0
        index = 0
        demo_ts = now().replace(hour=9,minute=30,second=0,microsecond=0)
        image = None
        was_active = False
        while not self.halt.is_set():
            self.poll()
            current = now()
            state = session(current,self.config.get('force'),self.config.get('holidays'))
            if self.config.get('demo'):
                state = '测试监控'
            permitted = active(state)
            if not permitted:
                if was_active:
                    c = minutes.close()
                    if c:
                        self.publish(c,self.minute_image)
                    minutes = Minutes(Engine(self.config.get('rules')))
                    sanity = Sanity()
                was_active = False
                self.status.emit(state+' · 等待交易时段')
                self.halt.wait(1)
                continue
            was_active = True
            if not self.config.get('demo') and minutes.key and current.replace(second=0,microsecond=0).isoformat()>minutes.key:
                c = minutes.close()
                if c:
                    self.publish(c,self.minute_image)
            interval = 1 if self.config.get('demo') else self.config['tick_seconds']
            if time.monotonic()-last_sample < interval:
                self.halt.wait(.25)
                continue
            last_sample = time.monotonic()
            try:
                if self.config.get('demo'):
                    tick,image = demo_tick(index,demo_ts+timedelta(minutes=index),self.config['code'])
                    index += 1
                else:
                    if not self.config.get('calibrated'):
                        raise CaptureError('首次实时运行需要：设置 → 区域标定 → 测试识别并确认')
                    if reader is None:
                        self.status.emit('正在加载本地 OCR 模型…')
                        reader = Reader()
                    _,rect = locate(self.config['window_keyword'])
                    frame = grab(rect)
                    tick,_ = reader.read(frame,self.config['layout'],self.config['code'])
                    image = privacy_image(frame)
                tick = sanity.check(tick)
                self.store.tick(tick)
                if tick.values.get('price') is None:
                    raise CaptureError('价格识别不可信，已丢弃；请检查标定和遮挡')
                self.ticked.emit(tick)
                c = minutes.add(tick)
                if c:
                    self.publish(c,self.minute_image)
                self.minute_image = image.copy()
                self.status.emit(('【演示：每秒回放一分钟】' if self.config.get('demo') else state+' · 监控中')+
                                 (' · 部分OCR字段无效' if tick.flags else ''))
            except CaptureError as e:
                self.report_error(str(e))
            except Exception as e:
                self.report_error('采集失败：'+type(e).__name__+'；检查依赖、窗口与标定')
            self.halt.wait(.1)
        # Deliberately do not emit an incomplete minute on manual stop.
        self.store.event('INFO 用户停止监控，未完成分钟不作完整分钟结论')
