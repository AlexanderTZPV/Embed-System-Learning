"""One-shot local capture probe. Does not upload or save screenshots or credentials."""
from datetime import datetime
from pathlib import Path
import traceback
from .core import now,Sanity
from .capture import locate,grab,Reader,exception_detail,calibration_report


def probe(config):
    report={'time':now().isoformat(),'code':config['code'],'steps':[],
            'note':'自检忽略交易时段；窗口定位不要求前台。测试时请保持同花顺可见。'}
    stage='窗口定位'
    try:
        _,rect=locate(config['window_keyword'],require_foreground=False)
        report['steps'].append({'stage':stage,'ok':True,'client_rect':list(rect)})
        stage='屏幕截图'
        image=grab(rect)
        report['steps'].append({'stage':stage,'ok':True,'image_size':list(image.size)})
        stage='加载OCR模型'
        reader=Reader()
        report['steps'].append({'stage':stage,'ok':True})
        stage='OCR识别'
        tick,raw=reader.read(image,config['layout'],config['code'])
        checked=calibration_report(tick,raw)
        report['steps'].append(dict(checked,stage=stage,ok=checked['can_save']))
        report['ok']=checked['can_save']
        if not report['ok']:
            report['error']='；'.join(checked['blockers'])
    except Exception as exc:
        report['ok']=False
        report['steps'].append({'stage':stage,'ok':False,'error':exception_detail(exc)})
        report['traceback']=''.join(traceback.format_exception(type(exc),exc,exc.__traceback__))
    return report


def write_report(root,report):
    import json
    folder=Path(root)/'logs'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'capture-check-{now():%Y%m%d-%H%M%S-%f}.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return path
