"""A blocked live capture must never substitute simulated market data."""
from copy import deepcopy
import pytest
from ths_watch.config import DEFAULT
from ths_watch.capture import CaptureError
from ths_watch import worker as module


@pytest.mark.parametrize('calibrated', [False, True])
def test_blocked_live_capture_does_not_fall_back_to_demo(tmp_path,monkeypatch,calibrated):
    config=deepcopy(DEFAULT)
    config.update(demo=False,force=True,calibrated=calibrated)
    worker=module.Worker(config,tmp_path)
    ticks,conclusions,statuses,attempts=[],[],[],[]
    worker.ticked.connect(ticks.append)
    worker.concluded.connect(conclusions.append)
    worker.status.connect(statuses.append)
    def report_and_stop(message):
        statuses.append(message)
        worker.stop()
    monkeypatch.setattr(worker,'report_error',report_and_stop)
    monkeypatch.setattr(module,'Reader',lambda:object())
    def no_window(keyword):
        attempts.append(keyword)
        raise CaptureError('未找到同花顺主窗口')
    monkeypatch.setattr(module,'locate',no_window)
    demo_calls=[]
    def no_demo(*args,**kwargs):
        demo_calls.append(True)
        worker.stop()
        raise AssertionError('Live mode called the demo generator')
    monkeypatch.setattr(module,'demo_tick',no_demo)
    worker.run()
    assert not demo_calls
    assert not ticks and not conclusions
    assert attempts==([config['window_keyword']] if calibrated else [])
    assert any(('未找到同花顺' if calibrated else '标定') in s for s in statuses)
    assert not (tmp_path/'demo').exists()
