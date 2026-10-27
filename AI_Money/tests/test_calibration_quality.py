"""Regression for actual user OCR text; not a claim of OCR accuracy on their screenshot."""
from copy import deepcopy
from PIL import Image
import pytest
from ths_watch.capture import Reader,calibration_report
from ths_watch.config import DEFAULT
from ths_watch.core import Tick,Sanity,number

USER_LINES={
 'price':('8.98 +0',.979899),
 'high':('8.45',.994596),'low':('9.25',.992169),
 'volume_total':('84.49万',.909669),'amount_total':('74677',.730001),
 'turnover_rate':('14.08%',.951728),'outer_vol':('45 4775',.841273),
 'limit_up':('.49 44',.662269),'limit_down':('.7 72',.819210),
}


def test_user_report_ambiguous_numbers_are_rejected_without_concatenation():
    reader=Reader.__new__(Reader)
    lines=iter([[('000530',.99)]]+[[v] for v in USER_LINES.values()])
    reader.lines=lambda *a,**k:next(lines)
    layout={key:[0,0,1,1] for key in ['identity',*USER_LINES]}
    tick,raw=reader.read(Image.new('RGB',(100,100)),layout,'000530')
    assert tick.values['price'] is None
    assert tick.values['outer_vol'] is None
    assert tick.values['limit_up'] is None and tick.values['limit_down'] is None
    assert tick.values['volume_total']==844900
    report=calibration_report(tick,raw)
    assert report['values']['high'] is None and report['values']['low'] is None
    assert report['values']['amount_total'] is None
    assert 'invalid_range' in report['flags']
    assert not report['can_save'] and report['blockers']
    assert any('最高价小于最低价' in i['message'] for i in report['issues'])
    assert report['raw']['price'][0][0]=='8.98 +0'


@pytest.mark.parametrize('text',['45 4775','8.98 +0','.49 44','.7 72','8.98 9.00'])
def test_internal_numeric_spaces_are_not_guessable(text):
    assert number(text) is None


def tick(values,confidence=None):
    return Tick('2026-10-27T10:00:00+08:00','000530',values,confidence or {})


def test_range_contradiction_is_not_fixed_by_swapping():
    checked=Sanity().check(tick({'price':8.98,'high':8.45,'low':9.25}))
    assert checked.values=={'price':8.98,'high':None,'low':None}
    report=calibration_report(checked,{})
    assert not report['can_save']


def test_price_outside_range_rejects_wrong_range_cells():
    checked=Sanity().check(tick({'price':8.98,'high':8.7,'low':8.4}))
    assert checked.values['high'] is None and checked.values['low'] is None
    assert 'price_outside_range' in checked.flags


def test_missing_amount_unit_cannot_make_absurd_average():
    values={'price':8.98,'high':9.25,'low':8.33,'volume_total':844900,'amount_total':74677}
    rejected=Sanity().check(tick(values))
    assert rejected.values['amount_total'] is None
    assert 'average_outside_range' in rejected.flags
    values['amount_total']=number('7.4677亿')
    accepted=Sanity().check(tick(values))
    assert accepted.values['amount_total']==746770000
    assert 'average_outside_range' not in accepted.flags


def test_low_confidence_is_shared_by_live_and_calibration():
    values={'price':8.98,'amount_total':74677}
    a=Sanity().check(tick(dict(values),{'amount_total':.73}))
    b=calibration_report(tick(dict(values),{'amount_total':.73}),{'amount_total':[('74677',.73)]})
    assert a.values==b['values']
    assert a.values['amount_total'] is None


def test_calibration_needs_test_and_retest_after_layout_change(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from ths_watch import ui
    app=QApplication.instance() or QApplication([])
    config=deepcopy(DEFAULT)
    d=ui.Calibration(Image.new('RGB',(1920,1016)),config)
    errors=[]
    monkeypatch.setattr(ui,'error',lambda parent,text:errors.append(text))
    assert not d.commit_button.isEnabled()
    d.commit()
    assert errors and not config['calibrated']
    report=calibration_report(tick({'price':8.98}),{'price':[('8.98',.99)]})
    d.show_test_result(report)
    assert d.commit_button.isEnabled()
    d.field.setCurrentText('price')
    d.select([.74,.38,.78,.40])
    assert not d.commit_button.isEnabled() and d.last_report is None
    d.show_test_result(report)
    d.reset_layout()
    assert d.last_report is None and not d.commit_button.isEnabled()
    assert not d.preview.pixmap().isNull()
    d.show_test_result(report)
    d.commit()
    assert config['calibrated']
    d.hide();d.deleteLater();app.processEvents()


def test_reference_quote_rows_keep_percentage_separate_from_absolute_change():
    from ths_watch.capture import LAYOUT
    assert LAYOUT['pct_change'][1:2] == LAYOUT['low'][1:2]
    assert LAYOUT['pct_change'][3] == LAYOUT['low'][3]
    assert LAYOUT['high'][1] > LAYOUT['price'][3]
    assert LAYOUT['low'][1] > LAYOUT['high'][3]
    assert LAYOUT['pct_change'][1] > LAYOUT['high'][3]
