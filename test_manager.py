"""Launch the status window on an isolated desktop without touching real data."""
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
import traceback

if len(sys.argv)>1 and sys.argv[1]=='--child':
    try:runpy.run_path(str(Path(__file__).with_name('manager.pyw')),run_name='__main__')
    except BaseException:
        Path(sys.argv[2]).write_text(traceback.format_exc(),encoding='utf-8')
        raise
elif __name__=='__main__':
    from isolated_desktop import IsolatedDesktop
    with tempfile.TemporaryDirectory(prefix='agent-daily-manager-') as tmp:
        os.environ['AGENT_DAILY_DATA']=tmp
        error=Path(tmp)/'error.txt'
        desktop=IsolatedDesktop()
        try:
            pythonw=Path(sys.executable).with_name('pythonw.exe')
            process=desktop.launch(str(pythonw),[str(Path(__file__).resolve()),'--child',str(error)],cwd=str(Path(__file__).parent))
            deadline=time.monotonic()+20;found=False
            while time.monotonic()<deadline and process.poll() is None:
                proof=desktop.verify_process_windows(process.pid,require_windows=False)
                found=any('积分助手' in w['title'] for w in proof['windows'])
                if found:break
                time.sleep(.2)
            if not found:
                print(error.read_text(encoding='utf-8') if error.exists() else '状态窗口未出现')
                raise SystemExit(1)
            print('状态窗口启动成功，未出现在用户桌面。')
        finally:desktop.close()
