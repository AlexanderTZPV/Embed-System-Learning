"""Windows client-area capture. Foreground checks prevent using covered market data."""
import sys
import re
import os
from PIL import Image, ImageOps
from .core import Tick, now, number, Sanity

# Approximate reference: 1920x1016 client, quote rows about 18 px apart.
# Verify the field crops on the actual client image; never silently migrate user layouts.
# price uses the small Latest cell, keeping the headline change value out of the crop.
LAYOUT = {
 'identity':[.706,.048,.853,.075],
 'price':[.747,.388,.782,.404],
 'pct_change':[.747,.424,.782,.441],
 'high':[.830,.406,.853,.423],
 'low':[.830,.424,.853,.441],
 'volume_total':[.747,.460,.782,.477],
 'amount_total':[.747,.478,.782,.495],
 'turnover_rate':[.813,.460,.853,.477],
 'volume_ratio':[.830,.442,.853,.459],
 'amplitude':[.747,.442,.782,.459],
 'outer_vol':[.747,.514,.782,.531],
 'inner_vol':[.813,.514,.853,.531],
 'limit_up':[.747,.496,.782,.513],
 'limit_down':[.830,.496,.853,.513],
 'wei_bi':[.729,.111,.769,.131],
}

class CaptureError(RuntimeError):
    pass

def exception_detail(exc):
    """Qualified names distinguish pywintypes.error from cv2.error."""
    name = type(exc).__module__+'.'+type(exc).__name__
    message = ' '.join(str(exc).split())
    code = getattr(exc,'winerror',None)
    if code is not None and str(code) not in message:
        message = f'WinError {code} '+message
    return name+(': '+message if message else '（无错误消息）')


def locate(keyword='同花顺', require_foreground=True):
    if sys.platform != 'win32':
        raise CaptureError('实时桌面采集需要 Windows；可使用演示或图片识别模式')
    import win32gui
    import win32con
    import win32process
    import pywintypes
    windows = []
    skipped = []
    def enum(hwnd, _):
        # Windows may disappear between any two Win32 calls, even during enumeration.
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if keyword not in win32gui.GetWindowText(hwnd):
                return True
            _,pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == os.getpid():
                return True
            left,top,right,bottom = win32gui.GetClientRect(hwnd)
            w,h = right-left,bottom-top
            if w > 800 and h > 500:
                windows.append((w*h,hwnd))
        except pywintypes.error as exc:
            skipped.append(exception_detail(exc))
        return True
    try:
        win32gui.EnumWindows(enum,None)
    except pywintypes.error as exc:
        raise CaptureError('窗口枚举失败：'+exception_detail(exc)) from exc
    if not windows:
        detail = '；部分候选窗口不可用：'+skipped[0] if skipped else ''
        raise CaptureError('未找到同花顺主窗口，请打开个股分时界面'+detail)
    hwnd = max(windows)[1]
    try:
        if require_foreground:
            fg = win32gui.GetForegroundWindow()
            if not fg:
                raise CaptureError('当前前台窗口暂不可用，采集暂停并自动重试')
            root = win32gui.GetAncestor(fg,win32con.GA_ROOT)
            if root not in {candidate for _,candidate in windows}:
                raise CaptureError('请保持同花顺在前台；其他窗口遮挡时暂停采集')
            hwnd = root
        if win32gui.IsIconic(hwnd):
            raise CaptureError('同花顺已最小化，采集暂停')
        left,top,right,bottom = win32gui.GetClientRect(hwnd)
        w,h = right-left,bottom-top
        if w <= 0 or h <= 0:
            raise CaptureError('同花顺客户区为空，请恢复窗口后重试')
        x,y = win32gui.ClientToScreen(hwnd,(left,top))
    except pywintypes.error as exc:
        raise CaptureError('读取同花顺窗口失败：'+exception_detail(exc)) from exc
    return hwnd, (x,y,w,h)

def grab(rect):
    import mss
    left,top,w,h = rect
    with mss.mss() as s:
        frame = s.grab({'left':left,'top':top,'width':w,'height':h})
        return Image.frombytes('RGB',frame.size,frame.rgb)

def crop(image, rect):
    w,h = image.size
    return image.crop((int(rect[0]*w),int(rect[1]*h),int(rect[2]*w),int(rect[3]*h)))

def privacy_image(image):
    """Whitelist chart + quote fields only; excludes top account row and all news/overlay."""
    w,h = image.size
    # Chart and quote strip, not upper account identity or bottom announcements.
    return image.crop((int(.04*w),int(.075*h),int(.853*w),int(.70*h)))

class Reader:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)

    def lines(self, image, detect=False):
        import numpy as np
        # Max-channel grayscale retains red/green numerals on a black background.
        rgb = np.asarray(image.convert('RGB'))
        mono = Image.fromarray(rgb.max(axis=2).astype('uint8'))
        mono = ImageOps.invert(ImageOps.autocontrast(mono)).resize((image.width*3,image.height*3))
        result,_ = self.engine(np.asarray(mono),use_det=detect,use_cls=False)
        return [(row[1],float(row[2])) if detect else (row[0],float(row[1])) for row in (result or [])]

    def read(self,image,layout,code,ts=None):
        identity = self.lines(crop(image,layout['identity']))
        codes = set(re.findall(r'(?<!\d)\d{6}(?!\d)', ''.join(t for t,c in identity if c>=.75)))
        if code not in codes:
            raise CaptureError('股票代码不匹配或未识别，请检查分时页并标定 identity 区域')
        values, confidence, raw = {}, {}, {}
        flags = []
        for name, rect in layout.items():
            if name == 'identity':
                continue
            lines = self.lines(crop(image,rect))
            raw[name] = lines
            # A calibrated cell must contain one value; multiple numeric tokens are ambiguous.
            candidates = [(number(t),c) for t,c in lines]
            candidates = [(n,c) for n,c in candidates if n is not None]
            if len(candidates) == 1:
                values[name], confidence[name] = candidates[0]
            else:
                values[name], confidence[name] = None, 0
                flags.append('unparsed:'+name)
        return Tick((ts or now()).isoformat(),code,values,confidence,flags),raw

def exclude_from_capture(widget):
    """Best-effort Windows 10 2004+ exclusion of our own overlay, not other applications."""
    if sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.user32.SetWindowDisplayAffinity
    fn.argtypes = [wintypes.HWND,wintypes.DWORD]
    fn.restype = wintypes.BOOL
    fn(wintypes.HWND(int(widget.winId())),0x11)

def physical_to_logical(rect):
    """Map Win32 physical desktop coordinates to Qt logical coordinates per monitor."""
    if sys.platform != 'win32':
        return rect
    import win32api
    from PySide6.QtGui import QGuiApplication
    x,y,w,h = rect
    monitor = win32api.MonitorFromPoint((x+w//2,y+h//2),2)
    info = win32api.GetMonitorInfo(monitor)
    screens = QGuiApplication.screens()
    screen = next((s for s in screens if s.name()==info['Device']),QGuiApplication.primaryScreen())
    left,top,_,_ = info['Monitor']
    ratio = screen.devicePixelRatio()
    g = screen.geometry()
    return (int(g.x()+(x-left)/ratio),int(g.y()+(y-top)/ratio),int(w/ratio),int(h/ratio))


def calibration_report(tick, raw):
    """Use live numeric checks during calibration, while preserving original OCR text."""
    tick = Sanity().check(tick)
    issues = []
    names = {'price':'最新价','high':'最高价','low':'最低价','volume_total':'总手',
             'amount_total':'成交额','turnover_rate':'换手率','outer_vol':'外盘','inner_vol':'内盘',
             'limit_up':'涨停','limit_down':'跌停'}
    for key,value in tick.values.items():
        if value is not None:
            continue
        text = ' / '.join(str(row[0]) for row in raw.get(key,[]))
        confidence = tick.confidence.get(key,0)
        if text and confidence < .75 and confidence > 0:
            reason = 'OCR置信度低于0.75，已拒绝'
        elif text:
            reason = '原文不是可信的单个数值，或与其他字段矛盾；请检查框选和单位'
        else:
            reason = '未识别到数值，请检查框选'
        issues.append({'field':key,'message':names.get(key,key)+'：'+reason,'raw':text})
    messages = {
        'invalid_range':'最高价小于最低价：两项均已拒绝，请重新对准对应行，不能自动交换。',
        'price_outside_range':'现价落在识别到的日内最高/最低范围外：范围数据已拒绝，请核对三个字段。',
        'invalid_limits':'涨停价低于跌停价：两项均已拒绝，请核对对应行。',
        'average_outside_range':'成交额与总手推导的均价超出日内范围：成交额已拒绝，请检查万/亿单位及总手单位。',
    }
    for flag in tick.flags:
        if flag in messages:
            issues.append({'field':'consistency','message':messages[flag]})
    blockers = []
    if tick.values.get('price') is None:
        blockers.append('price 未得到可信最新价，不能保存为已标定')
    for flag in ('invalid_range','price_outside_range','invalid_limits','above_limit','below_limit'):
        if flag in tick.flags:
            blockers.append(messages.get(flag,'最新价与涨跌停矛盾，请重新标定'))
    return {'values':tick.values,'confidence':tick.confidence,'raw':raw,'flags':tick.flags,
            'issues':issues,'can_save':not blockers,'blockers':blockers,
            'note':'置信度不是框选正确率。保存前逐项与同花顺核对；缺失字段会使相关规则不可用。'}
