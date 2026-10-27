import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from ths_watch.ui import Controller
from ths_watch.core import Engine

def test_overlay_source_priority_and_order(tmp_path):
    app=QApplication.instance() or QApplication([])
    ctrl=Controller(app,tmp_path)
    base=Engine().analyze({'price':9.53})
    def make(minute,source):
        return dict(base,ts=f'2026-10-26T{minute}:00+08:00',minute=minute,code='000530',source=source)
    ctrl.result(make('09:32','rule'))
    ctrl.result(make('09:31','rule'))
    ctrl.result(make('09:31','agent'))
    ctrl.result(make('09:31','llm'))
    assert ctrl.overlay.entries.count()==2
    assert ctrl.overlay.entries.item(0).data(256)['minute']=='09:32'
    assert ctrl.overlay.entries.item(1).data(256)['source']=='agent'
    ctrl.overlay.toolbar.toggle_input()
    assert ctrl.overlay.toolbar.clickthrough
    ctrl.overlay.toolbar.toggle_input()
    assert not ctrl.overlay.toolbar.clickthrough
    ctrl.overlay.hide();ctrl.overlay.toolbar.hide();ctrl.settings.hide()
    ctrl.tray.hide()
