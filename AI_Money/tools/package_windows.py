"""Build the download ZIP with Windows-safe batch bytes, even from LF Git blobs.

Run: python tools/package_windows.py [output.zip]
Only the explicit source/docs/test directories below are exported. Never package
.venv, runtime configuration, logs, credentials, build caches, or local archives.
"""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    '.gitignore', '.gitattributes', 'README.md', 'main.py',
    'requirements.txt', 'requirements-dev.txt',
    'setup.bat', 'run.bat', 'demo.bat', 'build.bat',
    '安装.bat', '启动.bat', '演示.bat', '打包exe.bat',
}
SOURCE_DIRS = ('ths_watch','tests','docs','examples','tools')


def package(destination):
    destination = Path(destination).resolve()
    paths = [ROOT/name for name in ROOT_FILES]
    for folder in SOURCE_DIRS:
        paths.extend(p for p in (ROOT/folder).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts
                     and p.suffix not in ('.pyc','.pyo'))
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            content = path.read_bytes()
            if path.suffix == '.bat':
                # No BOM or non-ASCII script commands, regardless of cmd.exe code page.
                content = content.decode('ascii').replace('\r\n','\n').replace('\n','\r\n').encode('ascii')
            archive.writestr(path.relative_to(ROOT).as_posix(),content)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP integrity check failed')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output',nargs='?',type=Path,default=ROOT.parent/'AI_Money-Windows.zip')
    args = parser.parse_args()
    result = package(args.output)
    print(f'{result}: {result.stat().st_size} bytes')
