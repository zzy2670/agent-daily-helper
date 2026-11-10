"""Per-user application discovery and local runtime state."""
from pathlib import Path
import json
import os
import re
import socket
import uuid
import winreg

DATA = Path(os.environ.get('AGENT_DAILY_DATA') or (Path(os.environ['LOCALAPPDATA']) / 'AgentDailyHelper'))
MUTEX = 'Local\\AgentDailyHelper'
CONTROL = DATA / 'control.json'

def sqlite_readonly_uri(path):
    return Path(path).resolve().as_uri() + '?mode=ro'

def _valid(path):
    path = Path(os.path.expandvars(str(path))).expanduser()
    return path.name.casefold()=='workbuddy.exe' and path.is_file() and (path.parent/'resources/app.asar').is_file()

def find_executable():
    explicit = os.environ.get('AGENT_DAILY_APP')
    saved = DATA/'app_path.txt'
    if not explicit and saved.exists():
        explicit = saved.read_text(encoding='utf-8-sig').strip()
    if explicit:
        expanded=Path(os.path.expandvars(explicit)).expanduser()
        if _valid(expanded):return expanded.resolve()
        raise FileNotFoundError('配置的国内版客户端路径不可用，请重新设置。')
    candidates=[Path(os.environ['LOCALAPPDATA'])/'Programs/WorkBuddy/WorkBuddy.exe']
    for name in ('ProgramFiles','ProgramFiles(x86)'):
        if os.environ.get(name):candidates.append(Path(os.environ[name])/'WorkBuddy/WorkBuddy.exe')
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for access in (winreg.KEY_READ|winreg.KEY_WOW64_64KEY, winreg.KEY_READ|winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,r'Software\Microsoft\Windows\CurrentVersion\Uninstall',0,access) as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root,winreg.EnumKey(root,i)) as key:
                                title=winreg.QueryValueEx(key,'DisplayName')[0]
                                if not re.match(r'^WorkBuddy(?:\s+\d|$)',str(title),re.I):continue
                                for attr in ('InstallLocation','DisplayIcon','UninstallString'):
                                    try:
                                        value=str(winreg.QueryValueEx(key,attr)[0]).strip()
                                        m=re.match(r'^"([^"]+)"|^([^,]+?\.exe)(?:,|\s|$)',value,re.I)
                                        p=Path((m.group(1) or m.group(2)) if m else value)
                                        candidates.append((p if p.is_dir() else p.parent)/'WorkBuddy.exe')
                                    except OSError:pass
                        except (OSError,ValueError):pass
            except OSError:pass
    for p in candidates:
        if _valid(p):return p.resolve()
    raise FileNotFoundError('未找到国内版客户端，请安装或指定 WorkBuddy.exe 路径。')

def choose_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]

def write_control(value):
    DATA.mkdir(parents=True,exist_ok=True)
    temp=DATA/('control-'+uuid.uuid4().hex+'.tmp')
    with temp.open('x',encoding='utf-8') as f:
        json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(temp,CONTROL)

def read_control():
    try:
        value=json.loads(CONTROL.read_text(encoding='utf-8'))
        if not (isinstance(value.get('pid'),int) and isinstance(value.get('created'),(int,float))
                and isinstance(value.get('port'),int) and 1024<=value['port']<=65535):return None
        return value
    except (OSError,ValueError,TypeError):return None
