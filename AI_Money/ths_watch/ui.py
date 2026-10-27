from __future__ import annotations
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QThread, Signal, QUrl, QObject, Slot
from PySide6.QtGui import QColor, QPainter, QPixmap, QImage, QIcon, QDesktopServices, QAction
from PySide6.QtWidgets import (QApplication,QWidget,QMainWindow,QFrame,QLabel,QPushButton,
    QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QSpinBox,QDoubleSpinBox,QCheckBox,
    QTabWidget,QTextEdit,QListWidget,QListWidgetItem,QComboBox,QFileDialog,QMessageBox,
    QSystemTrayIcon,QMenu,QDialog,QRubberBand,QTableWidget,QTableWidgetItem,QHeaderView)
from .config import load, save
from .core import RULES, now, session, atomic_json, Store
from .capture import locate,grab,Reader,LAYOUT,exclude_from_capture,physical_to_logical,crop,calibration_report,exception_detail
from .worker import Worker
from .ai import set_api_key

STYLE = '''
QWidget { font-family: "Microsoft YaHei UI", "Noto Sans CJK SC", sans-serif; font-size: 12px; color: #e2e8f0; }
QMainWindow, QDialog, QTabWidget::pane { background: #101827; }
QTabBar::tab { background:#172338; padding:12px 16px; } QTabBar::tab:selected { background:#234d64; color:#64ded2; }
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QTextEdit,QTableWidget { background:#182438; border:1px solid #334155; padding:6px; selection-background-color:#28546b; }
QPushButton { background:#25384e; border:1px solid #405469; padding:6px 12px; border-radius:5px; }
QPushButton:hover { background:#326079; } QPushButton:disabled { color:#6b7280; }
QCheckBox { spacing:8px; padding:4px; } QLabel { background:transparent; }
QListWidget { background:transparent; border:0; outline:0; } QListWidget::item { border-bottom:1px solid #334155; padding:8px; }
QToolTip { background:#182438; color:#e2e8f0; border:1px solid #49677b; }
'''
DISCLAIMER = '量价观察工具 · 不构成投资建议 · 主力意图无法仅凭截图确认'

def button(text,callback,layout):
    w = QPushButton(text)
    w.clicked.connect(callback)
    layout.addWidget(w)
    return w

def error(parent,text):
    QMessageBox.warning(parent,'同花顺盯盘助手',text)

class ImageCanvas(QLabel):
    selected = Signal(object)
    def __init__(self,image,max_height=None):
        super().__init__()
        self.source = image
        scale = min(1,1152/image.width)
        if max_height is not None:
            scale = min(scale,max_height/image.height)
        self.setFixedSize(max(1,int(image.width*scale)),max(1,int(image.height*scale)))
        rgb = image.convert('RGB')
        q = QImage(rgb.tobytes(),rgb.width,rgb.height,rgb.width*3,QImage.Format.Format_RGB888).copy()
        self.setPixmap(QPixmap.fromImage(q).scaled(self.size(),Qt.AspectRatioMode.IgnoreAspectRatio))
        self.band = QRubberBand(QRubberBand.Shape.Rectangle,self)
        self.start = None

    def mousePressEvent(self,e):
        self.start = e.position().toPoint()
        self.band.setGeometry(QRect(self.start,self.start))
        self.band.show()

    def mouseMoveEvent(self,e):
        if self.start is not None:
            self.band.setGeometry(QRect(self.start,e.position().toPoint()).normalized())

    def mouseReleaseEvent(self,e):
        if self.start is None:
            return
        r = self.band.geometry().intersected(self.rect())
        self.start = None
        if r.width()>3 and r.height()>3:
            self.selected.emit([r.x()/self.width(),r.y()/self.height(),
                                (r.x()+r.width())/self.width(),(r.y()+r.height())/self.height()])

    def highlight(self,rect):
        self.band.setGeometry(int(rect[0]*self.width()),int(rect[1]*self.height()),
                              int((rect[2]-rect[0])*self.width()),int((rect[3]-rect[1])*self.height()))
        self.band.show()

class OcrTest(QThread):
    result = Signal(object)
    def __init__(self,image,layout,code):
        super().__init__()
        self.image,self.layout,self.code = image,layout,code
    def run(self):
        try:
            t,raw = Reader().read(self.image,self.layout,self.code)
            self.result.emit(calibration_report(t,raw))
        except Exception as e:
            self.result.emit({'can_save':False,'blockers':['测试失败：'+exception_detail(e)]})

class CaptureProbe(QThread):
    result = Signal(object)
    def __init__(self,config,parent=None):
        super().__init__(parent)
        self.config = dict(config)
    def run(self):
        from .diagnostics import probe
        self.result.emit(probe(self.config))

class Calibration(QDialog):
    def __init__(self, image, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle('区域标定 · 选择字段后在图上拖框，仅框住该数值')
        self.config = config
        self.layout = dict(config['layout'])
        self.job = None
        self.last_report = None
        outer = QVBoxLayout(self)
        row = QHBoxLayout()
        self.field = QComboBox()
        self.field.addItems(list(dict.fromkeys(list(self.layout)+['main_net','net_xlarge','net_large','bid_volume','ask_volume','index_pct','macd','diff','dea','avg_price'])))
        row.addWidget(self.field)
        self.coords = QLabel()
        row.addWidget(self.coords)
        outer.addLayout(row)
        self.canvas = ImageCanvas(image,max_height=max(180,QApplication.primaryScreen().availableGeometry().height()-420))
        outer.addWidget(self.canvas)
        self.canvas.selected.connect(self.select)
        self.field.currentTextChanged.connect(self.highlight)
        self.preview = QLabel()
        self.preview.setFixedHeight(80)
        self.preview.setMinimumWidth(360)
        self.preview.setStyleSheet('background:black; border:1px solid #4b6475;')
        outer.addWidget(self.preview)
        outer.addWidget(QLabel('当前字段裁剪预览：必须只含对应数值；总手/金额保留万、亿等单位。'))
        self.validation_status = QLabel('尚未测试：请先检查各字段预览，再点击测试识别。')
        self.validation_status.setWordWrap(True)
        outer.addWidget(self.validation_status)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(150)
        self.output.setPlainText('identity 区域必须包含六位股票代码。其他字段只框数值，不包含标签或相邻列。\nmain_net 仅可标定带明确正负号的净流入数值；不能把“净流出”的正数当成净流入。')
        outer.addWidget(self.output)
        row = QHBoxLayout()
        self.test_button = button('测试识别',self.test,row)
        self.reset_button = button('恢复新版参考框',self.reset_layout,row)
        self.commit_button = button('保存标定（先测试并核对）',self.commit,row)
        self.commit_button.setEnabled(False)
        button('取消',self.reject,row)
        outer.addLayout(row)
        self.highlight(self.field.currentText())

    def highlight(self,key):
        if key in self.layout:
            self.canvas.highlight(self.layout[key])
            self.coords.setText(str([round(n,4) for n in self.layout[key]]))
            self.update_preview(self.layout[key])
        else:
            self.canvas.band.hide()
            self.preview.clear()
            self.coords.setText('尚未框选：请在图上拖框')

    def select(self,rect):
        self.layout[self.field.currentText()] = rect
        self.coords.setText(str([round(n,4) for n in rect]))
        self.last_report = None
        self.commit_button.setEnabled(False)
        self.validation_status.setText('框选已改变，需要重新测试。')
        self.update_preview(rect)

    def update_preview(self,rect):
        image = crop(self.canvas.source,rect).convert('RGB')
        if image.width < 1 or image.height < 1:
            self.preview.clear()
            return
        q = QImage(image.tobytes(),image.width,image.height,image.width*3,QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(q).scaled(self.preview.width(),80,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def reset_layout(self):
        if self.job and self.job.isRunning():
            return
        self.layout = deepcopy(LAYOUT)
        self.last_report = None
        self.commit_button.setEnabled(False)
        self.validation_status.setText('已恢复参考框，请重新测试。')
        self.output.setPlainText('已恢复新版参考框。请逐字段检查预览，必要时拖框微调，再测试。price 现在使用行情表“最新”的小字数值。')
        self.highlight(self.field.currentText())

    @Slot(object)
    def show_test_result(self,report):
        self.last_report = report
        if report.get('can_save'):
            count = len(report.get('issues',[]))
            self.validation_status.setText(f'价格及一致性校验通过；仍有{count}项字段问题，请与屏幕核对。' if count else '数值校验通过；请再与同花顺肉眼核对后保存。')
        else:
            self.validation_status.setText('不能保存：'+'；'.join(report.get('blockers',['识别失败'])))
        self.output.setPlainText(json.dumps(report,ensure_ascii=False,indent=2))
        self.commit_button.setEnabled(bool(report.get('can_save')))

    def test(self):
        self.test_button.setEnabled(False)
        self.commit_button.setEnabled(False)
        self.last_report = None
        self.canvas.setEnabled(False)
        self.field.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.job = OcrTest(self.canvas.source,dict(self.layout),self.config['code'])
        self.job.result.connect(self.show_test_result)
        self.job.finished.connect(lambda:self.test_button.setEnabled(True))
        self.job.finished.connect(lambda:self.canvas.setEnabled(True))
        self.job.finished.connect(lambda:self.field.setEnabled(True))
        self.job.finished.connect(lambda:self.reset_button.setEnabled(True))
        self.job.start()

    def commit(self):
        if self.job and self.job.isRunning():
            return
        if not self.last_report or not self.last_report.get('can_save'):
            error(self,'请先测试识别，并修正可信价格缺失或字段矛盾后再保存。')
            return
        self.config['layout'] = self.layout
        self.config['calibrated'] = True
        self.accept()

    def reject(self):
        if self.job and self.job.isRunning():
            return
        super().reject()

class Toolbar(QWidget):
    def __init__(self,overlay,controller):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|
                         Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.overlay = overlay
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        row = QHBoxLayout(self)
        row.setContentsMargins(0,0,0,0)
        self.start = button('▶ 开始演示' if controller.config['demo'] else '▶ 开始实时监控',controller.toggle,row)
        self.start.setStyleSheet('background:#176b63;')
        button('设置',controller.show_settings,row)
        self.lock = button('鼠标穿透：关',self.toggle_input,row)
        button('透明 / 新闻区',controller.toggle_opaque,row)
        button('隐藏',controller.hide_overlay,row)
        self.clickthrough = False

    def showEvent(self,event):
        exclude_from_capture(self)

    def toggle_input(self):
        self.clickthrough = not self.clickthrough
        g = self.overlay.geometry()
        self.overlay.setWindowFlag(Qt.WindowType.WindowTransparentForInput,self.clickthrough)
        self.overlay.setGeometry(g)
        self.overlay.show()
        self.lock.setText('鼠标穿透：'+('开' if self.clickthrough else '关'))

class Overlay(QWidget):
    def __init__(self, controller):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|
                         Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.controller = controller
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(420,170)
        self.drag = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12,8,12,8)
        self.header = QLabel('● 同花顺盯盘助手   '+controller.config['code'])
        self.header.setStyleSheet('color:#5eead4; font-weight:600; font-size:14px;')
        outer.addWidget(self.header)
        self.status = QLabel('已停止 · 拖动标题可移动窗口 · 控制条始终可点击')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)
        self.metrics = QLabel('等待监控数据')
        outer.addWidget(self.metrics)
        self.entries = QListWidget()
        self.entries.setWordWrap(True)
        outer.addWidget(self.entries,1)
        footer = QLabel(DISCLAIMER)
        footer.setStyleSheet('font-size:10px; color:#94a3b8;')
        outer.addWidget(footer)
        self.toolbar = Toolbar(self,controller)
        self.apply_geometry()

    def apply_geometry(self):
        g = self.controller.config['geometry']
        desktop = QApplication.primaryScreen().availableGeometry()
        w,h = min(g[2],desktop.width()),min(g[3],desktop.height()-45)
        x = max(desktop.left(), min(g[0], desktop.right()-w+1))
        y = max(desktop.top()+42, min(g[1],desktop.bottom()-h+1))
        self.setGeometry(x,y,w,h)

    def paintEvent(self,event):
        p = QPainter(self)
        alpha = 255 if self.controller.config.get('opaque_news') else int(self.controller.config['opacity']*255)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor('#355363'))
        p.setBrush(QColor(12,19,29,alpha))
        p.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),9,9)

    def moveEvent(self,e):
        if hasattr(self,'toolbar'):
            self.toolbar.move(self.x(),self.y()-38)

    def showEvent(self,e):
        exclude_from_capture(self)
        self.toolbar.move(self.x(),self.y()-38)
        self.toolbar.show()

    def mousePressEvent(self,e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 40:
            self.drag = e.globalPosition().toPoint()-self.pos()

    def mouseMoveEvent(self,e):
        if self.drag is not None:
            self.move(e.globalPosition().toPoint()-self.drag)

    def mouseReleaseEvent(self,e):
        self.drag = None
        self.controller.remember_geometry()

    def add(self,c):
        # Source priority is per minute; late results update their original row.
        rank = {'rule':0,'llm':1,'agent':2}
        item = None
        for i in range(self.entries.count()):
            candidate = self.entries.item(i)
            prev = candidate.data(Qt.ItemDataRole.UserRole)
            if prev['ts'] == c['ts']:
                if rank.get(prev['source'],0)>rank.get(c['source'],0):
                    return
                item = candidate
                break
        if item is None:
            item = QListWidgetItem()
            idx = 0
            while idx<self.entries.count() and self.entries.item(idx).data(Qt.ItemDataRole.UserRole)['ts']>c['ts']:
                idx += 1
            self.entries.insertItem(idx,item)
        item.setData(Qt.ItemDataRole.UserRole,c)
        tag = {'rule':'本地','llm':'AI','agent':'外部AI'}.get(c['source'],c['source'])
        item.setText(f"{c['minute']}  {c['state']}  [{tag}]\n{c['text']}")
        item.setToolTip(json.dumps(c,ensure_ascii=False,indent=2))
        item.setForeground(QColor({'danger':'#fb7185','warn':'#fbbf24','info':'#5eead4'}.get(c['severity'],'#cbd5e1')))
        while self.entries.count()>120:
            self.entries.takeItem(self.entries.count()-1)

    def closeEvent(self,event):
        if self.controller.closing:
            event.accept()
        else:
            event.ignore()
            self.controller.hide_overlay()

class Settings(QMainWindow):
    def __init__(self,controller):
        super().__init__()
        self.c = controller
        self.probe_job = None
        self.setWindowTitle('同花顺盯盘助手 · 系统设置')
        self.resize(970,700)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        title = QLabel('THS WATCH   /   本地盘面观察')
        title.setStyleSheet('font-size:22px; font-weight:600; color:#5eead4; padding:12px;')
        outer.addWidget(title)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs,1)
        self.controls = {}
        self.forms = {}
        for name in ('监控','区域标定','分析','日志与数据','外观','诊断'):
            page = QWidget()
            form = QFormLayout(page)
            self.tabs.addTab(page,name)
            self.forms[name] = form
        f = self.forms['监控']
        self.line(f,'股票代码','code')
        self.line(f,'股票名称（显示）','name')
        self.line(f,'同花顺窗口标题关键字','window_keyword')
        self.spin(f,'采样间隔（秒）','tick_seconds',1,30)
        self.check(f,'忽略交易日历和时段（仅测试）','force')
        self.check(f,'仅本次运行启用模拟行情（下次正常启动默认实时）','demo')
        self.mode_label = QLabel()
        self.mode_label.setStyleSheet('color:#5eead4; font-weight:600;')
        f.addRow(self.mode_label)
        f.addRow(QLabel('北京时间：09:15–09:25、09:30–11:30、13:00–15:00；每个完整分钟结束后输出。'))
        b = QPushButton('导入交易日历 JSON')
        b.clicked.connect(self.calendar)
        f.addRow(b)
        self.calendar_label = QLabel('内置2026年休市日；其他年份必须导入日历。')
        f.addRow(self.calendar_label)
        f = self.forms['区域标定']
        f.addRow(QLabel('截图默认布局仅供预填。首次使用请逐字段标定，核对代码、价格、量和换手。'))
        b = QPushButton('抓取同花顺窗口并标定（先停止监控）')
        b.clicked.connect(self.calibrate_live)
        f.addRow(b)
        b = QPushButton('从本地截图标定 / OCR测试')
        b.clicked.connect(self.calibrate_file)
        f.addRow(b)
        self.calibrated_label = QLabel()
        f.addRow(self.calibrated_label)
        row = QHBoxLayout()
        button('导出布局',self.export_layout,row)
        button('导入布局',self.import_layout,row)
        f.addRow(row)
        f.addRow(QLabel('盘口/资金等额外字段可在下拉框中选择后框选；不具备的数据保持缺失。\n遮挡检测采用前台窗口保守策略，不声称能识别所有遮挡。'))
        f = self.forms['分析']
        self.check(f,'启用 OpenAI 兼容 API（联网且按 API 计费）','llm_enabled')
        self.line(f,'API Base URL','base_url')
        self.line(f,'模型（需支持图像才能上传截图）','model')
        self.api_secret = QLineEdit()
        self.api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_secret.setPlaceholderText('留空保持原密钥；保存到 Windows 凭据库，不写配置文件')
        f.addRow('API Key',self.api_secret)
        self.check(f,'允许上传裁剪行情图（不勾选时仅上传指标）','upload_image')
        self.check(f,'读取外部 Agent 写回的文件','external_agent')
        f.addRow(QLabel('ChatGPT 网页订阅不含 API。普通聊天不会监听本地目录，需 API 或外部工作流。'))
        self.rules = QTableWidget(len(RULES),3)
        self.rules.setHorizontalHeaderLabels(['启用 / 规则','阈值 JSON（可编辑）','依据'])
        self.rules.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i,r in enumerate(RULES):
            opts = controller.config['rules'].get(r.id,{})
            item = QTableWidgetItem(r.name)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if opts.get('enabled',True) else Qt.CheckState.Unchecked)
            self.rules.setItem(i,0,item)
            self.rules.setItem(i,1,QTableWidgetItem(json.dumps({k+' '+op:opts.get('thresholds',{}).get(k+' '+op,v) for k,op,v in r.conditions},ensure_ascii=False)))
            item = QTableWidgetItem(r.explanation)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip(r.source+'\n'+str(r.conditions))
            self.rules.setItem(i,2,item)
        f.addRow(self.rules)
        f = self.forms['日志与数据']
        self.line(f,'截图根目录（空=runtime/captures）','capture_directory')
        self.line(f,'AI结果根目录（空=runtime/analysis）','analysis_directory')
        f.addRow(QLabel('每天一个 log + tick CSV + 结论 JSONL；截图每分钟一张 PNG + JSON。\n默认保留全部记录，不自动删除。请定期归档截图；低于500MB时停存图片。'))
        b = QPushButton('打开数据目录')
        b.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.c.root))))
        f.addRow(b)
        f = self.forms['外观']
        self.check(f,'结论区不透明（覆盖左下角个股新闻）','opaque_news')
        w = QDoubleSpinBox()
        w.setRange(.05,.95)
        w.setSingleStep(.05)
        w.setValue(controller.config['opacity'])
        self.controls['opacity'] = w
        f.addRow('透明模式背景不透明度',w)
        self.width = QSpinBox(); self.width.setRange(420,1600); self.width.setValue(controller.config['geometry'][2])
        self.height = QSpinBox(); self.height.setRange(170,1000); self.height.setValue(controller.config['geometry'][3])
        f.addRow('面板宽度',self.width)
        f.addRow('面板高度',self.height)
        b = QPushButton('吸附同花顺左下新闻区')
        b.clicked.connect(controller.snap_news)
        f.addRow(b)
        f = self.forms['诊断']
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        f.addRow(self.diagnostics)
        b = QPushButton('运行本机诊断')
        b.clicked.connect(self.diagnose)
        f.addRow(b)
        self.probe_button = QPushButton('测试采集并生成诊断（先停止监控）')
        self.probe_button.clicked.connect(self.start_capture_probe)
        f.addRow(self.probe_button)
        f.addRow(QLabel('演示仅本次生效：监控页勾选并保存后点击“开始演示”。每秒回放一分钟。正常启动不继承演示选择。'))
        row = QHBoxLayout()
        button('保存设置',self.apply,row)
        button('显示悬浮层',controller.show_overlay,row)
        button('退出软件',controller.quit,row)
        outer.addLayout(row)
        outer.addWidget(QLabel(DISCLAIMER))
        self.refresh()

    def refresh(self):
        self.mode_label.setText('当前数据源：模拟行情（不是实时行情）' if self.c.config['demo'] else '当前数据源：同花顺真实截图（需要标定）')
        self.calibrated_label.setText('标定状态：'+('已保存（仍需真机核对）' if self.c.config['calibrated'] else '未标定'))

    def line(self,form,label,key):
        w = QLineEdit(str(self.c.config.get(key,'')))
        self.controls[key] = w
        form.addRow(label,w)

    def spin(self,form,label,key,lo,hi):
        w = QSpinBox(); w.setRange(lo,hi); w.setValue(self.c.config[key])
        self.controls[key] = w; form.addRow(label,w)

    def check(self,form,label,key):
        w = QCheckBox(label); w.setChecked(self.c.config[key])
        self.controls[key] = w; form.addRow(w)

    def apply(self):
        if self.c.worker and self.c.worker.isRunning():
            error(self,'请先停止监控，等待当前采集结束后保存设置。')
            return
        c = dict(self.c.config)
        try:
            for key,w in self.controls.items():
                c[key] = w.isChecked() if isinstance(w,QCheckBox) else w.text().strip() if isinstance(w,QLineEdit) else w.value()
            if len(c['code']) != 6 or not c['code'].isdigit():
                raise ValueError('股票代码必须为六位数字')
            for k in ('capture_directory','analysis_directory'):
                if c[k] and not Path(c[k]).is_absolute():
                    raise ValueError('自定义目录必须使用绝对路径')
            if c['llm_enabled'] and not c['base_url'].startswith('https://'):
                raise ValueError('API 地址必须使用 HTTPS')
            rules = {}
            for i,r in enumerate(RULES):
                thresholds = json.loads(self.rules.item(i,1).text())
                if not isinstance(thresholds,dict) or any(not isinstance(v,(int,float)) for v in thresholds.values()):
                    raise ValueError('规则阈值必须是数值 JSON 对象')
                rules[r.id] = {'enabled':self.rules.item(i,0).checkState()==Qt.CheckState.Checked,'thresholds':thresholds}
            c['rules'] = rules
            c['geometry'] = [self.c.overlay.x(),self.c.overlay.y(),self.width.value(),self.height.value()]
            if c['code'] != self.c.config['code']:
                c['calibrated'] = False
            if self.api_secret.text():
                set_api_key(self.api_secret.text())
                self.api_secret.clear()
            save(self.c.root,c)
            self.c.config = c
            self.c.overlay.apply_geometry()
            self.c.overlay.update()
            self.refresh()
            self.c.reset_monitoring_view('设置已应用 · '+('点击开始演示' if c['demo'] else '点击开始实时监控'))
        except Exception as e:
            error(self,'保存失败：'+str(e))

    def calendar(self):
        path,_ = QFileDialog.getOpenFileName(self,'导入日历','','JSON (*.json)')
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            year = data['year']; days = data['holidays']
            from datetime import date
            if not isinstance(year,int) or not isinstance(days,list) or any(date.fromisoformat(x).year!=year for x in days):
                raise ValueError('日历格式错误')
            # Include every day of other years to fail closed, enforced in Controller.start too.
            self.c.config['holidays'] = days
            self.c.config['calendar_year'] = year
            save(self.c.root,self.c.config)
            self.calendar_label.setText(f'已导入 {year} 年日历，{len(days)} 个休市日期')
        except Exception as e:
            error(self,str(e))

    def open_calibration(self,image):
        if self.c.worker and self.c.worker.isRunning():
            error(self,'请先停止监控。')
            return
        dlg = Calibration(image,self.c.config,self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            save(self.c.root,self.c.config)
            self.refresh()

    def calibrate_live(self):
        try:
            self.c.overlay.hide(); self.c.overlay.toolbar.hide(); self.hide()
            _,rect = locate(self.c.config['window_keyword'],False)
            # Delay lets the desktop compositor remove the settings/overlay first.
            def shot():
                try:
                    im = grab(rect)
                    self.show(); self.c.show_overlay()
                    self.open_calibration(im)
                except Exception as e:
                    self.show(); self.c.show_overlay(); error(self,str(e))
            QTimer.singleShot(400,shot)
        except Exception as e:
            self.show(); self.c.show_overlay(); error(self,str(e))

    def calibrate_file(self):
        path,_ = QFileDialog.getOpenFileName(self,'选择同花顺客户区截图','','Images (*.png *.jpg *.bmp)')
        if path:
            try:
                self.open_calibration(Image.open(path).convert('RGB'))
            except Exception as e:
                error(self,str(e))

    def export_layout(self):
        path,_ = QFileDialog.getSaveFileName(self,'导出布局','layout.json','JSON (*.json)')
        if path:
            atomic_json(path,self.c.config['layout'])

    def import_layout(self):
        path,_ = QFileDialog.getOpenFileName(self,'导入布局','','JSON (*.json)')
        if path:
            try:
                layout = json.loads(Path(path).read_text(encoding='utf-8'))
                if not isinstance(layout,dict) or not {'identity','price'}.issubset(layout):
                    raise ValueError('缺少 identity 或 price')
                for rect in layout.values():
                    if len(rect)!=4 or not all(isinstance(n,(int,float)) and 0<=n<=1 for n in rect) or rect[0]>=rect[2] or rect[1]>=rect[3]:
                        raise ValueError('坐标必须为有效的[0,1]比例矩形')
                self.c.config['layout'] = layout
                self.c.config['calibrated'] = False
                save(self.c.root,self.c.config)
                self.refresh()
            except Exception as e:
                error(self,str(e))

    def start_capture_probe(self):
        if self.c.worker and self.c.worker.isRunning():
            error(self,'请先停止监控，等待停止完成后再测试采集。')
            return
        if self.probe_job and self.probe_job.isRunning():
            return
        self.probe_button.setEnabled(False)
        self.diagnostics.setPlainText('正在测试窗口定位、截图和OCR；本次不会上传或保存截图。')
        self.c.overlay.hide(); self.c.overlay.toolbar.hide(); self.hide()
        def start():
            self.probe_job = CaptureProbe(self.c.config,self)
            self.probe_job.result.connect(self.show_probe_result)
            self.probe_job.finished.connect(self.capture_probe_finished)
            self.probe_job.start()
        QTimer.singleShot(400,start)

    @Slot(object)
    def show_probe_result(self,report):
        from .diagnostics import write_report
        try:
            path = write_report(self.c.root,report)
            location = str(path)
        except OSError as exc:
            location = '报告保存失败：'+str(exc)
        self.diagnostics.setPlainText(location+'\n'+json.dumps(report,ensure_ascii=False,indent=2))
        if not self.c.closing:
            self.show(); self.raise_(); self.c.show_overlay()

    @Slot()
    def capture_probe_finished(self):
        self.probe_button.setEnabled(True)
        if self.c.closing:
            self.c.quit()

    def diagnose(self):
        import shutil,importlib.util
        lines = [f'Python {sys.version.split()[0]} / {sys.platform}',f'北京时间 {now().isoformat()}',
                 '交易状态 '+session(now()),f'剩余磁盘 {shutil.disk_usage(self.c.root).free//1024**2} MB']
        for name in ('PySide6','mss','rapidocr_onnxruntime','onnxruntime','keyring','win32gui'):
            lines.append(f'{name}: '+('可用' if importlib.util.find_spec(name) else '未安装/非本平台'))
        try:
            _,rect = locate(self.c.config['window_keyword'],False)
            lines.append('窗口客户区 '+str(rect))
        except Exception as e:
            lines.append(str(e))
        lines.append('标定 '+str(self.c.config['calibrated']))
        lines.append('API密钥仅检查存在性，不显示值。')
        try:
            from .ai import api_key
            lines.append('API Key: '+('已配置，尚不代表连通性' if api_key() else '未配置'))
        except Exception:
            lines.append('密钥库不可用')
        self.diagnostics.setPlainText('\n'.join(lines))

    def closeEvent(self,e):
        if self.c.closing:
            e.accept()
        else:
            e.ignore(); self.hide()

class Controller(QObject):
    def __init__(self,app,root,demo=False):
        super().__init__(app)
        self.app,self.root = app,Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.config = load(root)
        self.config['demo'] = bool(demo)
        self.worker = None
        self.closing = False
        self.overlay = Overlay(self)
        self.settings = Settings(self)
        pix = QPixmap(32,32); pix.fill(QColor('#0f766e'))
        p = QPainter(pix); p.setPen(QColor('white')); p.drawText(pix.rect(),Qt.AlignmentFlag.AlignCenter,'盯'); p.end()
        self.tray = QSystemTrayIcon(QIcon(pix),self.overlay)
        menu = QMenu()
        for label,callback in [('开始 / 停止',self.toggle),('显示悬浮层',self.show_overlay),('系统设置',self.show_settings),('退出',self.quit)]:
            a = menu.addAction(label); a.triggered.connect(callback)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.show_settings() if reason==QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.reset_monitoring_view()
        self.overlay.show()

    def reset_monitoring_view(self, message=None):
        demo = self.config['demo']
        mode = '演示' if demo else '实时监控'
        self.overlay.header.setText(f"● 【{mode}】{self.config['name']} {self.config['code']}")
        self.overlay.metrics.setText('等待模拟数据（不是实时行情）' if demo else '等待同花顺真实截图数据')
        self.overlay.entries.clear()
        self.overlay.status.setText(message or ('演示模式 · 仅本次运行' if demo else '实时模式 · 已停止 · 首次使用请标定'))
        self.overlay.toolbar.start.setText('▶ 开始演示' if demo else '▶ 开始实时监控')

    def toggle(self):
        if not self.settings.probe_button.isEnabled():
            error(self.settings,'正在测试采集，请等待自检完成后开始监控。')
            return
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.overlay.status.setText('正在停止，等待当前采集结束…')
            self.overlay.toolbar.start.setEnabled(False)
            return
        if self.config.get('holidays') is not None and self.config['calendar_year'] != now().year and not self.config['force'] and not self.config['demo']:
            error(self.settings,'导入的日历不是今年，请更新交易日历。')
            return
        self.reset_monitoring_view('正在启动'+('演示' if self.config['demo'] else '实时监控')+'…')
        self.worker = Worker(self.config,self.root)
        self.worker.status.connect(self.overlay.status.setText)
        self.worker.ticked.connect(self.tick)
        self.worker.concluded.connect(self.result)
        self.worker.finished.connect(self.stopped)
        self.worker.start()
        self.overlay.toolbar.start.setText('■ 停止演示' if self.config['demo'] else '■ 停止实时监控')

    @Slot(object)
    def tick(self,t):
        v = t.values
        def fmt(k):
            return '—' if v.get(k) is None else f'{v[k]:g}'
        self.overlay.header.setText(f"● {'【演示】' if self.config['demo'] else '【实时】'}{self.config['name']} {t.code}   {fmt('price')}")
        self.overlay.metrics.setText(f"换手 {fmt('turnover_rate')}%   总量 {fmt('volume_total')}手   量比 {fmt('volume_ratio')}   [{t.dt:%H:%M:%S}]")

    @Slot(object)
    def result(self,c):
        self.overlay.add(c)
        self.tray.setToolTip((c['minute']+' '+c['state'])[:120])

    @Slot()
    def stopped(self):
        self.overlay.toolbar.start.setEnabled(True)
        self.overlay.toolbar.start.setText('▶ 开始演示' if self.config['demo'] else '▶ 开始实时监控')
        # Keep fatal errors visible rather than reporting them as successful stops.
        if self.closing:
            self.quit()
        elif self.worker and self.worker.halt.is_set():
            self.overlay.status.setText('已停止演示' if self.config['demo'] else '已停止实时监控')

    def remember_geometry(self):
        r = self.overlay.geometry()
        self.config['geometry'] = [r.x(),r.y(),r.width(),r.height()]
        try:
            save(self.root,self.config)
        except OSError:
            self.overlay.status.setText('窗口位置保存失败，请检查磁盘')

    def toggle_opaque(self):
        self.config['opaque_news'] = not self.config['opaque_news']
        self.overlay.update()
        self.settings.controls['opaque_news'].setChecked(self.config['opaque_news'])
        self.remember_geometry()

    def snap_news(self):
        try:
            _,rect = locate(self.config['window_keyword'],False)
            x,y,w,h = physical_to_logical(rect)
            self.overlay.setGeometry(int(x+.014*w),int(y+.735*h),int(.685*w),int(.205*h))
            self.remember_geometry()
        except Exception as e:
            error(self.settings,str(e))

    def show_settings(self):
        self.settings.show(); self.settings.raise_(); self.settings.activateWindow()

    def show_overlay(self):
        self.overlay.show(); self.overlay.toolbar.show()

    def hide_overlay(self):
        self.overlay.hide(); self.overlay.toolbar.hide()
        if not self.tray.isVisible():
            self.show_settings()

    def quit(self):
        self.remember_geometry()
        self.closing = True
        if self.settings.probe_job and self.settings.probe_job.isRunning():
            if self.worker and self.worker.isRunning():
                self.worker.stop()
            self.overlay.status.setText('正在等待采集自检结束后退出…')
            return
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.overlay.status.setText('正在退出…')
        else:
            self.tray.hide(); self.app.quit()
