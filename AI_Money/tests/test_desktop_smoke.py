"""Run the delivered entry point and assert end-to-end artifacts + graceful process exit."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_entrypoint_demo_complete_pipeline(tmp_path):
    project=Path(__file__).resolve().parents[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen')
    proc=subprocess.run([sys.executable,str(project/'main.py'),'--demo','--data-dir',str(tmp_path),'--smoke-seconds','12'],
                        cwd=project,env=env,capture_output=True,text=True,timeout=25)
    assert proc.returncode==0, proc.stderr
    files=list((tmp_path/'demo/data/conclusions').glob('*.jsonl'))
    assert len(files)==1
    rows=[json.loads(s) for s in files[0].read_text(encoding='utf-8').splitlines()]
    assert len(rows)>=9
    assert all(c['simulated'] for c in rows)
    assert any('strong_pullup_dump' in c['rule_ids'] for c in rows)
    for c in rows:
        assert Path(c['snapshot_ref']).is_file()
        assert Path(c['snapshot_ref']).with_suffix('.json').is_file()
    assert len(list((tmp_path/'demo/logs').glob('*.log')))==1
    assert not list(tmp_path.rglob('crash-*'))
    assert not (tmp_path/'ths-watch.lock').exists()
