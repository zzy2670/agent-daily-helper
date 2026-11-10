"""User-owned enable switch; pausing never needs administrator rights."""
from runtime_config import DATA
import json
import os
import uuid

FILE=DATA/'settings.json'

def is_enabled():
    try:return json.loads(FILE.read_text(encoding='utf-8')).get('enabled') is True
    except FileNotFoundError:return True
    except (OSError,ValueError):return False

def set_enabled(enabled):
    DATA.mkdir(parents=True,exist_ok=True)
    tmp=DATA/('settings-'+uuid.uuid4().hex+'.tmp')
    data=json.dumps({'enabled':bool(enabled)}).encode('utf-8')
    with tmp.open('xb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(tmp,FILE)
