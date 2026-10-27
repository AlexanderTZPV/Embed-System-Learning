"""Windows entry point. All user data is adjacent to the source/executable by default."""
import argparse
import os
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description='同花顺盯盘助手')
    parser.add_argument('--demo',action='store_true',help='Simulated data; never live quotes')
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--smoke-seconds',type=int,default=0,help=argparse.SUPPRESS)
    args = parser.parse_args()
    # Must be set before Qt creates any HWND; physical pixel coordinates for mss/Win32.
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication,QMessageBox
    from ths_watch.ui import Controller,STYLE
    base = Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
    root = (args.data_dir or base/'runtime').resolve()
    root.mkdir(parents=True,exist_ok=True)
    app = QApplication(sys.argv[:1])
    app.setApplicationName('THS Watch')
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    # QLockFile also detects a stale lock left by an abnormal exit.
    lock = QLockFile(str(root/'ths-watch.lock'))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(100):
        QMessageBox.information(None,'同花顺盯盘助手','这个数据目录已有实例正在运行。请从托盘打开已有窗口。若要退出演示，请先停止，在设置取消模拟行情并保存；或从设置退出软件后运行 run.bat。')
        return 1
    controller = Controller(app,root,args.demo)
    if args.demo:
        QTimer.singleShot(100,controller.toggle)
    if args.smoke_seconds:
        QTimer.singleShot(args.smoke_seconds*1000,controller.quit)
    def crash(exc_type,value,tb):
        import traceback
        from ths_watch.core import now
        folder = root/'logs'; folder.mkdir(parents=True,exist_ok=True)
        (folder/f'crash-{now():%Y%m%d-%H%M%S}.log').write_text(''.join(traceback.format_exception(exc_type,value,tb)),encoding='utf-8')
        QMessageBox.critical(None,'程序错误','错误已写入本地 crash 日志。请退出后检查。')
    sys.excepthook = crash
    result = app.exec()
    lock.unlock()
    return result

if __name__ == '__main__':
    raise SystemExit(main())
