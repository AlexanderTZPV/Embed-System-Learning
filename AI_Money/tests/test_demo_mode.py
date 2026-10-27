"""Simulated data is an explicit session choice, never a sticky startup default."""
import json
import os
from copy import deepcopy

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ths_watch import ui
from ths_watch.config import DEFAULT, load, save
from ths_watch.core import Engine, Tick


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controllers(app):
    created = []

    def create(root, demo=False):
        controller = ui.Controller(app, root, demo=demo)
        created.append(controller)
        return controller

    yield create
    for controller in reversed(created):
        if controller.worker and controller.worker.isRunning():
            controller.worker.stop()
        controller.overlay.hide()
        controller.overlay.toolbar.hide()
        controller.settings.hide()
        controller.tray.hide()
        controller.overlay.toolbar.deleteLater()
        controller.overlay.deleteLater()
        controller.settings.deleteLater()
        controller.deleteLater()
    app.processEvents()


def test_legacy_saved_demo_does_not_set_normal_startup_mode(tmp_path):
    path = tmp_path / 'config/app.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'demo': True, 'code': '600519', 'tick_seconds': 5}), encoding='utf-8')
    restored = load(tmp_path)
    assert restored['demo'] is False
    assert restored['code'] == '600519'
    assert restored['tick_seconds'] == 5


def test_saving_demo_session_preserves_memory_but_restart_defaults_live(tmp_path):
    session = deepcopy(DEFAULT)
    session.update(demo=True, name='测试股票', tick_seconds=5)
    original = deepcopy(session)
    save(tmp_path, session)
    assert session == original
    persisted = json.loads((tmp_path / 'config/app.json').read_text(encoding='utf-8'))
    assert not persisted.get('demo', False)
    assert persisted['name'] == '测试股票'
    restored = load(tmp_path)
    assert restored['demo'] is False
    assert restored['tick_seconds'] == 5


def test_explicit_demo_geometry_save_cannot_make_next_launch_simulated(tmp_path, controllers):
    simulated = controllers(tmp_path, demo=True)
    assert simulated.config['demo'] is True
    simulated.overlay.move(48, 100)
    simulated.remember_geometry()
    # Saving position must neither stop this explicit demo nor select demo next time.
    assert simulated.config['demo'] is True
    live = controllers(tmp_path)
    assert live.config['demo'] is False
    assert not live.settings.controls['demo'].isChecked()
    assert '演示' not in live.overlay.header.text()
    assert '开始实时监控' in live.overlay.toolbar.start.text()
    assert '真实截图' in live.overlay.metrics.text()


class FakeWorker(QObject):
    status = Signal(str)
    ticked = Signal(object)
    concluded = Signal(object)
    finished = Signal()

    def __init__(self, config, root):
        super().__init__()
        self.config = deepcopy(config)
        self.running = False
        # Controller.stopped expects the worker's manual-stop marker.
        import threading
        self.halt = threading.Event()

    def isRunning(self):
        return self.running

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.halt.set()
        self.finished.emit()


def test_same_session_switch_to_live_clears_demo_and_selects_live_worker(tmp_path, controllers, monkeypatch):
    monkeypatch.setattr(ui, 'Worker', FakeWorker)
    monkeypatch.setattr(ui, 'error', lambda parent, text: pytest.fail(text))
    controller = controllers(tmp_path, demo=True)
    controller.toggle()
    assert controller.worker.config['demo'] is True
    controller.tick(Tick('2026-10-26T09:31:00+08:00', '000530', {'price': 99.99}))
    result = Engine().analyze({'price': 99.99})
    result.update(ts='2026-10-26T09:31:00+08:00', minute='09:31', code='000530')
    controller.result(result)
    assert '演示' in controller.overlay.header.text()
    assert controller.overlay.entries.count() == 1

    controller.toggle()  # Stop the old source before applying a different source.
    assert not controller.worker.isRunning()
    controller.settings.controls['demo'].setChecked(False)
    controller.settings.apply()
    assert controller.config['demo'] is False
    assert '演示' not in controller.overlay.header.text()
    assert '99.99' not in controller.overlay.header.text()
    assert '真实截图' in controller.overlay.metrics.text()
    assert controller.overlay.entries.count() == 0
    assert '开始实时监控' in controller.overlay.toolbar.start.text()

    controller.toggle()
    assert controller.worker.config['demo'] is False
    assert '停止实时监控' in controller.overlay.toolbar.start.text()
    assert not load(tmp_path)['demo']
