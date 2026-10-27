"""Windows client-area capture. Foreground checks prevent using covered market data."""
import sys
import re
import os
from PIL import Image, ImageOps
from .core import Tick, now, number

# Relative to the supplied 1920x~1040 client layout, not the inaccurate draft regions.
# Verify on the user's physical display with the calibration editor before live use.
LAYOUT = {
 'identity':[.706,.048,.853,.075],
 'price':[.737,.078,.782,.105],
 'pct_change':[.802,.082,.826,.104],
 'high':[.830,.394,.853,.412],
 'low':[.830,.411,.853,.430],
 'volume_total':[.747,.452,.782,.471],
 'amount_total':[.747,.469,.782,.488],
 'turnover_rate':[.813,.452,.853,.471],
 'volume_ratio':[.830,.430,.853,.449],
 'amplitude':[.750,.430,.782,.449],
 'outer_vol':[.750,.505,.782,.524],
 'inner_vol':[.818,.505,.853,.524],
 'limit_up':[.758,.487,.782,.506],
 'limit_down':[.833,.487,.853,.506],
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
        for name, rect in layout.items():
            if name == 'identity':
                continue
            lines = self.lines(crop(image,rect))
            raw[name] = lines
            # A calibrated cell must contain one value; multiple numeric tokens are ambiguous.
            candidates = [(number(t.replace(' ','')),c) for t,c in lines]
            candidates = [(n,c) for n,c in candidates if n is not None]
            if len(candidates) == 1:
                values[name], confidence[name] = candidates[0]
            else:
                values[name], confidence[name] = None, 0
        return Tick((ts or now()).isoformat(),code,values,confidence),raw

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
