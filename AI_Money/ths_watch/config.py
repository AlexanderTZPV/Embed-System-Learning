from copy import deepcopy
from pathlib import Path
import json
from .capture import LAYOUT
from .core import atomic_json

DEFAULT = {
 'code':'000530','name':'冰山冷热','window_keyword':'同花顺','tick_seconds':3,
 'force':False,'demo':False,'calibrated':False,'layout':LAYOUT,'holidays':None,
 'llm_enabled':False,'upload_image':False,'base_url':'https://api.openai.com/v1',
 'model':'gpt-4.1-mini','external_agent':False,'rules':{},
 'geometry':[28,775,645,235], 'opacity':0.58, 'opaque_news':True,
 'capture_directory':'', 'analysis_directory':'', 'calendar_year':2026,
}

def load(root):
    path = Path(root)/'config/app.json'
    c = deepcopy(DEFAULT)
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        c.update(saved)
    return c

def save(root, c):
    atomic_json(Path(root)/'config/app.json',c)
