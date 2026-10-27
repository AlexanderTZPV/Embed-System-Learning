"""Regression coverage for the delivered Windows launchers and their ZIP bytes.

Static checks run on Linux as well. Actual cmd.exe behavior is tested only on
Windows; passing static checks must not be reported as Windows execution.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import venv
import zipfile

import pytest


PROJECT = Path(__file__).resolve().parents[1]
CANONICAL = ('setup.bat', 'run.bat', 'demo.bat', 'build.bat')
WRAPPERS = {
    '安装.bat': 'setup.bat',
    '启动.bat': 'run.bat',
    '演示.bat': 'demo.bat',
    '打包exe.bat': 'build.bat',
}
SCRIPTS = (*CANONICAL, *WRAPPERS)


def assert_cmd_bytes(data: bytes, label: str) -> None:
    """cmd.exe parsing must not depend on checkout conversion or code page."""
    assert data, f'{label} is empty'
    assert not data.startswith((b'\xef\xbb\xbf', b'\xff\xfe', b'\xfe\xff')), f'{label} has a BOM'
    assert b'\r\n' in data, f'{label} must use Windows CRLF line endings'
    rest = data.replace(b'\r\n', b'')
    assert b'\n' not in rest and b'\r' not in rest, f'{label} has mixed or LF-only line endings'
    try:
        data.decode('ascii')
    except UnicodeDecodeError:
        pytest.fail(f'{label} contains code-page-dependent non-ASCII commands')


@pytest.mark.parametrize('name', SCRIPTS)
def test_delivered_batch_file_bytes(name):
    path = PROJECT / name
    assert path.is_file(), f'Missing delivered launcher: {name}'
    assert_cmd_bytes(path.read_bytes(), name)


@pytest.mark.parametrize('name,target', WRAPPERS.items())
def test_chinese_filename_wrappers_call_ascii_entrypoints(name, target):
    text = (PROJECT / name).read_bytes().decode('ascii').lower()
    # Wrapper filenames may be Unicode; executable command text never needs to be.
    assert target in text
    assert '%*' in text, f'{name} must forward caller arguments'
    assert 'call ' in text, f'{name} must return from the child batch script'


def test_git_preserves_crlf_for_future_windows_checkouts():
    attributes = next((p for p in (PROJECT / '.gitattributes', PROJECT.parent / '.gitattributes') if p.is_file()), None)
    if attributes is None:
        pytest.skip('Standalone ZIP extraction has no parent Git attributes file')
    rules = [line.split() for line in attributes.read_text(encoding='utf-8').splitlines() if not line.lstrip().startswith('#')]
    assert any(row and row[0] == '*.bat' and 'text' in row and 'eol=crlf' in row for row in rules)


def test_download_zip_contains_the_fixed_batch_bytes():
    archive = PROJECT.parent / 'AI_Money-Windows.zip'
    if not archive.is_file():
        pytest.skip('Download artifact is built separately from a standalone source checkout')
    with zipfile.ZipFile(archive) as bundle:
        members = {PurePosixPath(name).name: name for name in bundle.namelist() if name.lower().endswith('.bat')}
        assert set(SCRIPTS).issubset(members)
        for name in SCRIPTS:
            packed = bundle.read(members[name])
            assert_cmd_bytes(packed, f'ZIP:{name}')
            assert packed == (PROJECT / name).read_bytes(), f'ZIP still contains stale {name}'


@pytest.fixture(scope='module')
def windows_project(tmp_path_factory):
    if sys.platform != 'win32':
        pytest.skip('Requires actual Windows cmd.exe')
    root = tmp_path_factory.mktemp('windows-launchers') / 'space path 中文'
    root.mkdir()
    for name in SCRIPTS:
        shutil.copyfile(PROJECT / name, root / name)
    # No pip, Qt, network, or application dependencies are needed to test dispatch.
    venv.EnvBuilder(with_pip=False).create(root / '.venv')
    (root / 'requirements.txt').write_text('',encoding='ascii')
    (root / 'main.py').write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "Path(__file__).with_name('invocation.json').write_text(json.dumps(sys.argv[1:],ensure_ascii=False),encoding='utf-8')\n"
        "raise SystemExit(7)\n",
        encoding='utf-8',
    )
    return root


def invoke_cmd(script: Path):
    comspec = os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe')
    # Supply a raw Windows command line so /s sees the intended outer quote pair.
    command = f'"{comspec}" /d /s /c ""{script}" "two words" "unicode-雪人""'
    environment = dict(os.environ, THS_NO_PAUSE='1', PYTHONIOENCODING='utf-8')
    return subprocess.run(command, executable=comspec, cwd=script.parent, env=environment,
                          capture_output=True, text=True, errors='replace', timeout=20)


@pytest.mark.parametrize('name,prefix', [
    ('run.bat', []),
    ('demo.bat', ['--demo']),
    ('启动.bat', []),
    ('演示.bat', ['--demo']),
])
def test_real_cmd_forwarding_and_exit_code(windows_project, name, prefix):
    result = invoke_cmd(windows_project / name)
    assert result.returncode == 7, result.stdout + result.stderr
    args = json.loads((windows_project / 'invocation.json').read_text(encoding='utf-8'))
    assert args == [*prefix, 'two words', 'unicode-雪人']


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires actual Windows cmd.exe')
def test_real_cmd_missing_environment_fails_without_hanging(tmp_path):
    root = tmp_path / 'missing environment 中文'
    root.mkdir()
    shutil.copyfile(PROJECT / 'run.bat', root / 'run.bat')
    result = invoke_cmd(root / 'run.bat')
    assert result.returncode != 0
    assert not (root / 'invocation.json').exists()


@pytest.mark.parametrize('name', ['setup.bat', '安装.bat'])
def test_real_cmd_setup_repairs_pip_and_succeeds(windows_project, name):
    # Empty requirements isolate launcher/venv bootstrapping from package network access.
    result = invoke_cmd(windows_project / name)
    assert result.returncode == 0, result.stdout + result.stderr
    verify = subprocess.run(
        [str(windows_project / '.venv/Scripts/python.exe'), '-m', 'pip', '--version'],
        capture_output=True, text=True, errors='replace', timeout=20,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
