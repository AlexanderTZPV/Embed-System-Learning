from copy import deepcopy
import json
from pathlib import Path
from PIL import Image
import pytest
from ths_watch import diagnostics as diag
from ths_watch import worker as workers
from ths_watch.config import DEFAULT
from ths_watch.core import Tick

# Deliberately identical class names from different libraries, like the user screenshot.
CvError=type('error',(Exception,),{'__module__':'cv2'})
WinError=type('error',(Exception,),{'__module__':'pywintypes'})


def setup_probe(monkeypatch):
    config=deepcopy(DEFAULT)
    config.update(calibrated=True,force=True,api_key='not-to-be-logged')
    monkeypatch.setattr(diag,'locate',lambda *a,**k:(10,(0,0,1200,800)))
    monkeypatch.setattr(diag,'grab',lambda r:Image.new('RGB',(r[2],r[3])))
    class Reader:
        def read(self,*args):
            return Tick('2026-10-27T09:30:00+08:00','000530',{'price':8.58},{'price':.98}),{'price':[['8.58',.98]]}
    monkeypatch.setattr(diag,'Reader',Reader)
    return config


def test_probe_success_and_local_report_has_no_secret_or_screenshot(tmp_path,monkeypatch):
    c=setup_probe(monkeypatch)
    result=diag.probe(c)
    assert result['ok']
    assert len(result['steps'])==4
    assert result['steps'][-1]['values']['price']==8.58
    path=diag.write_report(tmp_path,result)
    assert path.parent==tmp_path/'logs'
    assert json.loads(path.read_text(encoding='utf-8'))['ok']
    assert 'not-to-be-logged' not in path.read_text(encoding='utf-8')
    assert not list(tmp_path.rglob('*.png'))


@pytest.mark.parametrize('stage,target',[
    ('窗口定位','locate'),('屏幕截图','grab'),('加载OCR模型','Reader'),('OCR识别','read')])
def test_probe_reports_stage_qualified_class_message_and_trace(monkeypatch,stage,target):
    c=setup_probe(monkeypatch)
    def fail(*args,**kwargs):
        raise CvError('cv::cvtColor: bad channel count')
    if target=='read':
        class Reader:
            read=fail
        monkeypatch.setattr(diag,'Reader',Reader)
    else:
        monkeypatch.setattr(diag,target,fail)
    result=diag.probe(c)
    assert not result['ok']
    assert result['steps'][-1]['stage']==stage
    assert 'cv2.error' in result['steps'][-1]['error']
    assert 'bad channel count' in result['steps'][-1]['error']
    assert 'Traceback' in result['traceback']


def test_live_worker_preserves_real_error_details_and_local_trace(tmp_path,monkeypatch):
    config=deepcopy(DEFAULT)
    config.update(calibrated=True,force=True)
    w=workers.Worker(config,tmp_path)
    ticks=[];messages=[]
    w.ticked.connect(ticks.append)
    report=w.report_error
    def stop_after_error(message):
        messages.append(message);report(message);w.stop()
    monkeypatch.setattr(w,'report_error',stop_after_error)
    class Reader:
        def read(self,*args):
            raise CvError('cv::cvtColor: bad channel count')
    monkeypatch.setattr(workers,'Reader',Reader)
    monkeypatch.setattr(workers,'locate',lambda *a:(10,(0,0,1200,800)))
    monkeypatch.setattr(workers,'grab',lambda r:Image.new('RGB',(1200,800)))
    w.run()
    assert not ticks
    assert any('OCR识别' in s and 'cv2.error' in s and 'bad channel count' in s for s in messages)
    log='\n'.join(p.read_text(encoding='utf-8') for p in (tmp_path/'logs').glob('*.log'))
    assert 'Traceback' in log and 'cv2.error' in log


def test_settings_capture_probe_runs_off_gui_and_shows_report(tmp_path,monkeypatch):
    from PySide6.QtCore import QEventLoop,QTimer
    from PySide6.QtWidgets import QApplication
    from ths_watch import ui
    app=QApplication.instance() or QApplication([])
    c=ui.Controller(app,tmp_path)
    monkeypatch.setattr(diag,'probe',lambda config:{'ok':False,'code':config['code'],'steps':[{'stage':'窗口定位','ok':False,'error':'pywintypes.error: (1400, GetClientRect)'}]})
    messages=[]
    monkeypatch.setattr(ui,'error',lambda parent,text:messages.append(text))
    c.settings.start_capture_probe()
    assert not c.settings.probe_button.isEnabled()
    c.toggle()
    assert messages and c.worker is None
    loop=QEventLoop()
    QTimer.singleShot(1500,loop.quit)
    loop.exec()
    assert c.settings.probe_job is not None and not c.settings.probe_job.isRunning()
    assert c.settings.probe_button.isEnabled()
    assert '1400' in c.settings.diagnostics.toPlainText()
    assert len(list((tmp_path/'logs').glob('capture-check-*.json')))==1
    c.overlay.hide();c.overlay.toolbar.hide();c.settings.hide();c.tray.hide()
