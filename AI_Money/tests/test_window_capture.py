"""Window-selection regression checks with fake Win32 APIs, not a Windows claim."""
from __future__ import annotations

import os
import sys
from types import ModuleType

import pytest

from ths_watch import capture


class WinError(Exception):
    """Match pywintypes.error's public fields and module-qualified type name."""
    def __init__(self, code, function, message):
        super().__init__(code, function, message)
        self.winerror = code
        self.funcname = function
        self.strerror = message


WinError.__name__ = 'error'
WinError.__module__ = 'pywintypes'


class FakeWindows:
    def __init__(self):
        self.windows = {}
        self.foreground = 101
        self.roots = {}
        self.failures = {}
        self.calls = []

    def add(self, hwnd=101, **options):
        self.windows[hwnd] = dict(
            title='同花顺 - A股分时走势', rect=(0, 0, 1920, 1016),
            origin=(0, 24), pid=os.getpid() + 1000, visible=True, iconic=False,
        )
        self.windows[hwnd].update(options)
        return hwnd

    def call(self, function, hwnd=None):
        self.calls.append((function, hwnd))
        failure = self.failures.get((function, hwnd))
        if failure:
            raise failure
        return self.windows.get(hwnd)

    def EnumWindows(self, callback, extra):
        self.call('EnumWindows')
        for hwnd in list(self.windows):
            if callback(hwnd, extra) is False:
                break

    def IsWindow(self, hwnd):
        self.call('IsWindow', hwnd)
        return hwnd in self.windows

    def IsWindowVisible(self, hwnd):
        return self.call('IsWindowVisible', hwnd)['visible']

    def GetWindowText(self, hwnd):
        return self.call('GetWindowText', hwnd)['title']

    def GetClientRect(self, hwnd):
        return self.call('GetClientRect', hwnd)['rect']

    def GetWindowThreadProcessId(self, hwnd):
        return 777, self.call('GetWindowThreadProcessId', hwnd)['pid']

    def IsIconic(self, hwnd):
        return self.call('IsIconic', hwnd)['iconic']

    def GetForegroundWindow(self):
        self.call('GetForegroundWindow')
        return self.foreground

    def GetAncestor(self, hwnd, kind):
        self.call('GetAncestor', hwnd)
        assert kind == 2
        return self.roots.get(hwnd, hwnd)

    def ClientToScreen(self, hwnd, point):
        origin = self.call('ClientToScreen', hwnd)['origin']
        return origin[0] + point[0], origin[1] + point[1]


def invoke_windows(monkeypatch, fake, **kwargs):
    """Keep the global sys.platform override scoped to the single locate call."""
    gui = ModuleType('win32gui')
    for name in ('EnumWindows', 'IsWindow', 'IsWindowVisible', 'GetWindowText',
                 'GetClientRect', 'IsIconic', 'GetForegroundWindow', 'GetAncestor',
                 'ClientToScreen'):
        setattr(gui, name, getattr(fake, name))
    con = ModuleType('win32con')
    con.GA_ROOT = 2
    process = ModuleType('win32process')
    process.GetWindowThreadProcessId = fake.GetWindowThreadProcessId
    errors = ModuleType('pywintypes')
    errors.error = WinError
    with monkeypatch.context() as scoped:
        scoped.setattr(capture.sys, 'platform', 'win32')
        for name, module in [('win32gui', gui), ('win32con', con),
                             ('win32process', process), ('pywintypes', errors)]:
            scoped.setitem(sys.modules, name, module)
        return capture.locate(**kwargs)


def test_valid_client_dimensions_and_screen_origin(monkeypatch):
    fake = FakeWindows()
    fake.add(origin=(-1920, 24), rect=(0, 0, 1600, 900))
    hwnd, rect = invoke_windows(monkeypatch, fake)
    assert hwnd == 101
    assert rect == (-1920, 24, 1600, 900)


def test_client_width_and_height_are_rect_differences(monkeypatch):
    fake = FakeWindows()
    fake.add(rect=(800, 700, 1800, 1300))
    hwnd, rect = invoke_windows(monkeypatch, fake)
    assert hwnd == 101
    assert rect[2:] == (1000, 600)


@pytest.mark.parametrize('function', ['GetWindowText', 'GetClientRect'])
def test_stale_window_during_enumeration_does_not_abort_other_candidates(monkeypatch, function):
    fake = FakeWindows()
    fake.add(99)
    fake.failures[(function, 99)] = WinError(1400, function, 'Invalid window handle')
    fake.add(101)
    hwnd, rect = invoke_windows(monkeypatch, fake)
    assert hwnd == 101
    assert rect[2:] == (1920, 1016)


def test_zero_foreground_is_an_actionable_capture_error(monkeypatch):
    fake = FakeWindows()
    fake.add()
    fake.foreground = 0
    with pytest.raises(capture.CaptureError):
        invoke_windows(monkeypatch, fake)
    assert not any(name == 'GetAncestor' and hwnd == 0 for name, hwnd in fake.calls)


def test_zero_foreground_does_not_block_explicit_calibration_capture(monkeypatch):
    fake = FakeWindows()
    fake.add()
    fake.foreground = 0
    assert invoke_windows(monkeypatch, fake, require_foreground=False)[0] == 101


def test_own_process_windows_are_not_market_candidates(monkeypatch):
    fake = FakeWindows()
    fake.add(pid=os.getpid(), title='同花顺盯盘助手 - 系统设置')
    with pytest.raises(capture.CaptureError):
        invoke_windows(monkeypatch, fake, require_foreground=False)


def test_matching_foreground_root_wins_over_larger_background_window(monkeypatch):
    fake = FakeWindows()
    fake.add(101, rect=(0, 0, 1920, 1080))
    fake.add(202, rect=(0, 0, 1200, 800), origin=(100, 100))
    fake.foreground = 999
    fake.roots[999] = 202
    hwnd, rect = invoke_windows(monkeypatch, fake)
    assert hwnd == 202
    assert rect == (100, 100, 1200, 800)


@pytest.mark.parametrize('function,handle,code', [
    ('EnumWindows', None, 5),
    ('GetAncestor', 101, 1400),
    ('ClientToScreen', 101, 1400),
])
def test_system_api_failure_retains_qualified_type_and_numeric_code(monkeypatch, function, handle, code):
    fake = FakeWindows()
    fake.add()
    fake.failures[(function, handle)] = WinError(code, function, 'System API failed')
    with pytest.raises(capture.CaptureError) as failed:
        invoke_windows(monkeypatch, fake)
    message = str(failed.value)
    assert str(code) in message
    assert 'pywintypes' in message


def test_minimized_market_window_pauses_capture(monkeypatch):
    fake = FakeWindows()
    fake.add(iconic=True)
    with pytest.raises(capture.CaptureError, match='最小化'):
        invoke_windows(monkeypatch, fake)
