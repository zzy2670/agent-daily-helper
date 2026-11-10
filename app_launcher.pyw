"""Normal, user-initiated launch with a temporary local control channel."""
import ctypes
import subprocess
import time
import psutil
import win32api
import win32event
from runtime_config import MUTEX,choose_port,find_executable,write_control

mutex=win32event.CreateMutex(None,False,MUTEX)
owned=False
try:
    result=win32event.WaitForSingleObject(mutex,240_000)
    if result not in (win32event.WAIT_OBJECT_0,win32event.WAIT_ABANDONED):
        raise RuntimeError('后台检查仍在运行，请稍后再打开。')
    owned=True
    exe=find_executable()
    existing=[]
    for proc in psutil.process_iter(['exe']):
        try:
            if (proc.info['exe'] or '').casefold()==str(exe).casefold():existing.append(proc.pid)
        except (psutil.AccessDenied,psutil.NoSuchProcess):pass
    if existing:
        # The app handles a normal second-instance request. A new debugging
        # channel cannot be added to an already running process.
        subprocess.Popen([str(exe)],cwd=exe.parent)
    else:
        port=choose_port()
        child=subprocess.Popen([str(exe),'--remote-debugging-address=127.0.0.1',
          f'--remote-debugging-port={port}','--force-renderer-accessibility=complete'],cwd=exe.parent)
        created=psutil.Process(child.pid).create_time()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline and child.poll() is None:
            listeners=[c for c in psutil.net_connections(kind='tcp') if c.status==psutil.CONN_LISTEN
              and c.laddr.port==port and c.laddr.ip=='127.0.0.1' and c.pid==child.pid]
            if listeners:
                write_control({'pid':child.pid,'created':created,'port':port})
                break
            time.sleep(.3)
        else:
            raise RuntimeError('客户端已启动，但后台控制尚未连接；请稍后重试。')
except (OSError,RuntimeError,psutil.Error) as exc:
    ctypes.windll.user32.MessageBoxW(None,str(exc),'AI 每日助手',0x10)
finally:
    if owned:win32event.ReleaseMutex(mutex)
    win32api.CloseHandle(mutex)
