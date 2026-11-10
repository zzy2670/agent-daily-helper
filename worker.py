"""WorkBuddy daily rewards: localhost CDP virtual pointer, no shared input."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone, timedelta
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sqlite3
import sys
import threading
import time
from urllib.parse import urlsplit, unquote
import psutil
import win32event
import win32api
from cdp_client import CDP, targets
from settings import is_enabled
from runtime_config import DATA, MUTEX, find_executable, choose_port, read_control

BASE = Path(__file__).resolve().parent
try:
    EXE = find_executable()
except FileNotFoundError:
    EXE = None
SGT = timezone(timedelta(hours=8), 'UTC+08')
TASK_NAME = 'AgentDaily-Helper'
SCRIPT = (BASE/'page_ui.js').read_text(encoding='utf-8')
log = logging.getLogger('workbuddy_points')

class RetryLater(Exception):
    pass

def day_key(now=None):
    return (now or datetime.now(SGT)).astimezone(SGT).date().isoformat()

def timestamp():
    return datetime.now(SGT).isoformat(timespec='seconds')

def event(code, **fields):
    # Callers pass controlled labels/counters only. Never log a page URL or body.
    log.info(json.dumps({'time':timestamp(),'event':code,**fields},ensure_ascii=False))

def configure_logging():
    DATA.mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(DATA/'activity.log',maxBytes=512_000,backupCount=3,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    log.setLevel(logging.INFO)
    log.addHandler(handler)

class Store:
    def __init__(self, directory=DATA):
        directory.mkdir(exist_ok=True)
        self.db=sqlite3.connect(directory/'state.sqlite3',timeout=5)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS daily
          (day TEXT PRIMARY KEY,status TEXT NOT NULL,updated TEXT NOT NULL,
           detail TEXT NOT NULL,claim_actions INTEGER NOT NULL DEFAULT 0)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS runs
          (id INTEGER PRIMARY KEY,time TEXT,source TEXT,result TEXT,detail TEXT)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS runtime (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        self.db.commit()
    def get(self, day):
        row=self.db.execute('SELECT status,detail,claim_actions FROM daily WHERE day=?',(day,)).fetchone()
        return dict(zip(('status','detail','claim_actions'),row)) if row else None
    def intent(self, day):
        # This durable row is committed before the virtual click is dispatched.
        with self.db:
            row=self.get(day)
            if row and row['status'] in ('success','pending'):
                return False
            self.db.execute('''INSERT INTO daily(day,status,updated,detail,claim_actions)
              VALUES(?,'pending',?,'awaiting_ui_confirmation',1)
              ON CONFLICT(day) DO UPDATE SET status='pending',updated=excluded.updated,
              detail=excluded.detail,claim_actions=daily.claim_actions+1''',(day,timestamp()))
        return True
    def success(self, day, detail='today_claimed_visible', close_target=None):
        with self.db:
            self.db.execute('''INSERT INTO daily(day,status,updated,detail,claim_actions)
             VALUES(?,'success',?,?,0) ON CONFLICT(day) DO UPDATE SET
             status='success',updated=excluded.updated,detail=excluded.detail''',(day,timestamp(),detail))
            if close_target:
                self.db.execute("INSERT OR REPLACE INTO runtime(key,value) VALUES('pending_close',?)",
                  (json.dumps(close_target),))
    def pending_close(self):
        row=self.db.execute("SELECT value FROM runtime WHERE key='pending_close'").fetchone()
        return json.loads(row[0]) if row else None
    def update_close(self, target):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO runtime(key,value) VALUES('pending_close',?)",(json.dumps(target),))
    def close_done(self):
        with self.db:self.db.execute("DELETE FROM runtime WHERE key='pending_close'")
    def cancel_unsent_intent(self, day):
        with self.db:
            self.db.execute("""UPDATE daily SET status='error',updated=?,
              detail='no_claim_input_was_dispatched',claim_actions=MAX(0,claim_actions-1)
              WHERE day=? AND status='pending'""",(timestamp(),day))
    def finish(self, source, result, detail):
        with self.db:
            self.db.execute('INSERT INTO runs(time,source,result,detail) VALUES(?,?,?,?)',
                (timestamp(),source,result,detail))
            self.db.execute('DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 300)')
    def close(self):
        self.db.close()

class GUIInfo(ctypes.Structure):
    _fields_=[('cbSize',wintypes.DWORD),('flags',wintypes.DWORD),
      ('active',wintypes.HWND),('focus',wintypes.HWND),('capture',wintypes.HWND),
      ('menu',wintypes.HWND),('move',wintypes.HWND),('caret',wintypes.HWND),('rect',wintypes.RECT)]

u32=ctypes.WinDLL('user32',use_last_error=True)
u32.GetForegroundWindow.restype=wintypes.HWND
u32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
u32.GetGUIThreadInfo.argtypes=[wintypes.DWORD,ctypes.POINTER(GUIInfo)]

def native_context():
    hwnd=u32.GetForegroundWindow()
    pid=wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    gui=GUIInfo();gui.cbSize=ctypes.sizeof(gui)
    u32.GetGUIThreadInfo(0,ctypes.byref(gui))
    return {'foreground':int(hwnd or 0),'focus':int(gui.focus or 0),'pid':pid.value}

def app_processes():
    found=[]
    if EXE is None:return found
    for p in psutil.process_iter(['pid','name','exe','create_time']):
        try:
            if (p.info['exe'] or '').casefold()==str(EXE).casefold():
                found.append(p)
        except (psutil.AccessDenied,psutil.NoSuchProcess):
            pass
    return found

def validate_endpoint(allowed_pids=None, port=None):
    if EXE is None:raise RetryLater('client_not_found')
    if port is None:
        record=read_control()
        if not record:raise RetryLater('control_channel_unavailable')
        try:
            if psutil.Process(record['pid']).create_time()!=record['created']:
                raise RetryLater('control_channel_unavailable')
        except psutil.NoSuchProcess:
            raise RetryLater('control_channel_unavailable')
        if allowed_pids is not None and record['pid'] not in allowed_pids:
            raise RetryLater('control_listener_not_owned_by_worker')
        allowed_pids={record['pid']}
        port=record['port']
    listeners=[c for c in psutil.net_connections(kind='tcp')
      if c.status==psutil.CONN_LISTEN and c.laddr.port==port]
    if not listeners:
        raise RetryLater('control_channel_unavailable')
    for c in listeners:
        if c.laddr.ip not in ('127.0.0.1','::1') or not c.pid:
            raise RetryLater('unsafe_control_listener')
        if allowed_pids is not None and c.pid not in allowed_pids:
            raise RetryLater('control_listener_not_owned_by_worker')
        try:
            if psutil.Process(c.pid).exe().casefold()!=str(EXE).casefold():
                raise RetryLater('control_port_owned_by_other_program')
        except psutil.NoSuchProcess:
            raise RetryLater('control_process_exited')
    owners={c.pid for c in listeners}
    if len(owners)!=1:raise RetryLater('control_listener_ambiguous')
    owner=next(iter(owners))
    expected=(EXE.parent/'resources/app.asar/renderer/index.html').as_posix().casefold()
    pages=[]
    for t in targets(port):
        parsed=urlsplit(t.get('url',''))
        path=unquote(parsed.path).lstrip('/').casefold()
        if t.get('type')=='page' and parsed.scheme=='file' and path==expected:
            ws=urlsplit(t.get('webSocketDebuggerUrl',''))
            if ws.hostname not in ('127.0.0.1','localhost','::1') or ws.port!=port:
                raise RetryLater('unsafe_page_endpoint')
            pages.append(t)
    if len(pages)!=1:
        raise RetryLater('main_window_ambiguous' if pages else 'main_window_not_ready')
    pages[0]['_owner_pid']=owner
    pages[0]['_owner_created']=psutil.Process(owner).create_time()
    pages[0]['_port']=port
    return pages[0]

class FocusGuard:
    def __init__(self):
        self.initial=native_context()
        self.stop=threading.Event()
        self.violation=False
        self.samples=0
        self.pids={p.pid for p in app_processes()}
        self.last_context=(self.initial['foreground'],self.initial['focus'])
        if self.initial['pid'] in self.pids:
            raise RetryLater('workbuddy_in_foreground_wait_for_user')
        self.thread=threading.Thread(target=self.watch,daemon=True)
        self.thread.start()
    def watch(self):
        refreshed=0
        while not self.stop.wait(.05):
            self.samples+=1
            if time.monotonic()-refreshed>.5:
                self.pids.update(p.pid for p in app_processes())
                refreshed=time.monotonic()
            if native_context()['pid'] in self.pids:
                self.violation=True
    def check(self):
        current=native_context()
        if self.violation or current['pid'] in self.pids:
            raise RetryLater('workbuddy_became_foreground_stop')
        context=(current['foreground'],current['focus'])
        if context!=self.last_context:
            event('input_context_changed_waiting_for_stability')
            stable_since=time.monotonic();deadline=stable_since+20
            while time.monotonic()<deadline:
                time.sleep(.1)
                current=native_context()
                if self.violation or current['pid'] in self.pids:
                    raise RetryLater('workbuddy_became_foreground_stop')
                following=(current['foreground'],current['focus'])
                if following!=context:context=following;stable_since=time.monotonic()
                if time.monotonic()-stable_since>=5:break
            else:raise RetryLater('input_context_not_stable')
            self.last_context=context
    def close(self):
        self.stop.set();self.thread.join(timeout=1)
        final=native_context()
        event('input_guard',samples=self.samples,workbuddy_activated=self.violation,
              foreground_unchanged=self.initial['foreground']==final['foreground'],
              keyboard_focus_unchanged=self.initial['focus']==final['focus'])

class PageUI:
    def __init__(self, target, guard):
        self.cdp=CDP(target)
        self.guard=guard
        self.claim_dispatched=False
        # Emulates focus only inside the renderer; never activates its OS window.
        self.cdp.call('Emulation.setFocusEmulationEnabled',{'enabled':True})
    def query(self, kind):
        return self.cdp.evaluate(SCRIPT.replace('KIND',json.dumps(kind),1))
    def click(self, kind, expected_day):
        self.guard.check()
        if day_key()!=expected_day:
            raise RetryLater('day_changed_before_action')
        deadline=time.monotonic()+8
        point=None
        while time.monotonic()<deadline:
            self.guard.check()
            point=self.query(kind)
            if point:break
            time.sleep(.3)
        if not point:raise RetryLater(kind+'_not_clickable_or_obscured')
        # Query again immediately before dispatch to catch animation/layout changes.
        point=self.query(kind)
        if not point:
            raise RetryLater(kind+'_changed_before_action')
        self.guard.check()
        for typ in ('mouseMoved','mousePressed','mouseReleased'):
            if day_key()!=expected_day and typ!='mouseReleased':
                raise RetryLater('day_changed_before_action')
            if kind=='claim' and typ=='mousePressed':
                # Set before sending: a lost response must count as uncertain.
                self.claim_dispatched=True
            self.cdp.call('Input.dispatchMouseEvent',{'type':typ,'x':point['x'],'y':point['y'],
              'button':'none' if typ=='mouseMoved' else 'left',
              'clickCount':0 if typ=='mouseMoved' else 1})
        event('virtual_click',control=kind)
        time.sleep(.35)
        self.guard.check()
    def dismiss_ads(self, day):
        for _ in range(3):
            original=self.query('dismiss')
            if not original:
                return
            self.click('dismiss',day)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                following=self.query('dismiss')
                if not following or following!=original:break
                time.sleep(.3)
            else:raise RetryLater('promotion_did_not_close')
        if self.query('dismiss'):
            raise RetryLater('promotion_did_not_close')
    def wait(self, predicate, seconds):
        deadline=time.monotonic()+seconds
        last=None
        while time.monotonic()<deadline:
            self.guard.check()
            last=self.query('status')
            if predicate(last):return last
            time.sleep(.5)
        return last
    def open_card(self, day):
        self.dismiss_ads(day)
        status=self.query('status')
        if status['loginRequired']:
            raise RetryLater('login_required')
        if status['startupError']:
            raise RetryLater('application_startup_error')
        if not status['menuOpen']:
            self.click('profile',day)
            status=self.wait(lambda s:s['menuOpen'],8)
        if not status or not status['menuOpen']:
            raise RetryLater('account_menu_did_not_open')
        self.click('menu',day)
        status=self.wait(lambda s:s['claimed'] or bool(s['claim']),12)
        if not status or not (status['claimed'] or status['claim']):
            raise RetryLater('reward_activity_missing_or_obscured')
        return status
    def close(self):
        try:self.cdp.call('Emulation.setFocusEmulationEnabled',{'enabled':False})
        except Exception:pass
        self.cdp.close()

def close_isolated(desktop, process, owned, port):
    # Capture descendants while the parent still exists, and bind PID to start time.
    try:
        root=psutil.Process(process.pid)
        if root.create_time()==owned.get(process.pid):
            for p in [root,*root.children(recursive=True)]:
                owned[p.pid]=p.create_time()
    except psutil.NoSuchProcess:pass
    try:
        target=validate_endpoint(allowed_pids=set(owned),port=port)
        connection=CDP(target)
        try:connection.call('Browser.close')
        finally:connection.close()
    except Exception:pass
    deadline=time.monotonic()+20
    def living():
        out=[]
        for pid,created in owned.items():
            try:
                p=psutil.Process(pid)
                if p.create_time()==created and p.is_running():out.append(p)
            except psutil.NoSuchProcess:pass
        return out
    while time.monotonic()<deadline and living():time.sleep(.3)
    remaining=living()
    for p in remaining:
        try:p.terminate()
        except psutil.NoSuchProcess:pass
    _,remaining=psutil.wait_procs(remaining,timeout=5)
    for p in remaining:
        try:p.kill()
        except psutil.NoSuchProcess:pass
    if remaining:psutil.wait_procs(remaining,timeout=5)
    # Closing the job is an additional guarantee for children created late.
    desktop.close()
    if living():raise RetryLater('owned_isolated_processes_did_not_exit')
    event('isolated_app_closed',owned_processes=len(owned))

def close_identity(target):
    root=psutil.Process(target['_owner_pid'])
    if root.create_time()!=target['_owner_created']:
        raise RetryLater('close_target_identity_changed')
    family={p.pid:p.create_time() for p in [root,*root.children(recursive=True)]}
    return {'pid':root.pid,'created':root.create_time(),'family':family,'port':target['_port']}

def close_existing(store, target_identity, guard):
    """Close only the exact previously verified normal instance, never a new one."""
    pid=target_identity['pid'];created=target_identity['created']
    family={int(p):ct for p,ct in target_identity.get('family',{}).items()}
    family.setdefault(pid,created)
    root=None
    try:
        root=psutil.Process(pid)
        if root.create_time()!=created:
            root=None
    except psutil.NoSuchProcess:
        pass
    if root:
        target=validate_endpoint(allowed_pids={pid},port=target_identity.get('port'))
        if target['_owner_created']!=created:
            raise RetryLater('close_target_identity_changed')
        family.update({p.pid:p.create_time() for p in [root,*root.children(recursive=True)]})
        target_identity['family']=family
        store.update_close(target_identity)
        connection=CDP(target)
        try:
            guard.check()
            if psutil.Process(pid).create_time()!=created:
                raise RetryLater('close_target_identity_changed')
            try:connection.call('Browser.close')
            except Exception:pass  # Socket closure is expected when the browser exits.
        finally:connection.close()
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        alive=[]
        for p,ct in family.items():
            try:
                proc=psutil.Process(p)
                if proc.create_time()==ct and proc.is_running():alive.append(proc)
            except psutil.NoSuchProcess:pass
        if not alive:
            store.close_done();event('existing_app_closed');return
        # If the exact root is gone, only its already-bound WorkBuddy helpers
        # may be cleaned up. A new same-path instance is never included.
        root_alive=any(p.pid==pid for p in alive)
        if not root_alive:
            guard.check()
            for child in alive:
                try:
                    if child.exe().casefold()==str(EXE).casefold():child.terminate()
                except psutil.NoSuchProcess:pass
        time.sleep(.3)
    raise RetryLater('application_exit_not_confirmed')

def run(store, source='manual', verify_only=False):
    today=day_key()
    existing=store.get(today)
    if existing and existing['status']=='success' and not verify_only:
        closing=store.pending_close()
        if closing:
            guard=None
            try:
                guard=FocusGuard();guard.check()
                close_existing(store,closing,guard)
            except RetryLater as exc:
                store.finish(source,'deferred',str(exc));event('deferred',reason=str(exc));return 2
            except Exception as exc:
                store.finish(source,'failed','close_'+type(exc).__name__);return 3
            finally:
                if guard:guard.close()
        event('skip_already_successful',day=today)
        store.finish(source,'skipped','today_already_successful')
        return 0
    isolated=None;process=None;ui=None;guard=None;owned={};new_intent=False;runtime_port=None
    try:
        if EXE is None:raise RetryLater('client_not_found')
        guard=FocusGuard()
        if not app_processes():
            from isolated_desktop import IsolatedDesktop
            isolated=IsolatedDesktop()
            runtime_port=choose_port()
            process=isolated.launch(str(EXE),['--remote-debugging-address=127.0.0.1',
              f'--remote-debugging-port={runtime_port}','--force-renderer-accessibility=complete'])
            try:owned[process.pid]=psutil.Process(process.pid).create_time()
            except psutil.NoSuchProcess:pass
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                try:
                    target=validate_endpoint(allowed_pids=set(owned),port=runtime_port);break
                except (RetryLater,TimeoutError,OSError):time.sleep(1)
            else:raise RetryLater('isolated_startup_timeout')
            proof=isolated.verify_process_windows(process.pid)
            for pid in proof['process_ids']:
                try:owned[pid]=psutil.Process(pid).create_time()
                except psutil.NoSuchProcess:pass
            event('isolated_app_started')
        else:
            target=validate_endpoint()
        guard.check()
        ui=PageUI(target,guard)
        # Initial page loading can leave a fading skeleton that is not an ad.
        status=ui.wait(lambda s:s['profile'] or s['loginRequired'],30)
        if not status or status['loginRequired']:
            raise RetryLater('login_required')
        status=ui.open_card(today)
        if status['claimed']:
            if day_key()!=today:raise RetryLater('day_changed_during_verification')
            closing=None if verify_only or isolated else close_identity(target)
            store.success(today,close_target=closing)
            store.finish(source,'success','today_claimed_visible')
            event('verified_success',day=today)
            if closing:
                ui.close();ui=None;guard.check();close_existing(store,closing,guard)
            return 0
        if existing and existing['status']=='pending':
            raise RetryLater('pending_result_not_confirmed_no_second_click')
        if verify_only:
            store.finish(source,'verified','not_claimed_no_click')
            event('verify_only_unclaimed',day=today)
            return 0
        if day_key()!=today:raise RetryLater('day_changed_before_intent')
        if not ui.query('claim'):
            raise RetryLater('claim_not_clickable')
        if not store.intent(today):
            raise RetryLater('claim_already_pending')
        new_intent=True
        # Once intent is committed, any exception leaves pending for reconciliation.
        ui.click('claim',today)
        status=ui.wait(lambda s:s['claimed'],30)
        if status and status['claimed'] and day_key()==today:
            closing=None if isolated else close_identity(target)
            store.success(today,close_target=closing)
            store.finish(source,'success','today_claimed_visible_after_click')
            event('verified_success',day=today)
            if closing:
                ui.close();ui=None;guard.check();close_existing(store,closing,guard)
            return 0
        raise RetryLater('pending_result_not_confirmed_no_second_click')
    except RetryLater as exc:
        if new_intent and ui and not ui.claim_dispatched:
            store.cancel_unsent_intent(today)
        detail=str(exc)
        event('deferred',reason=detail)
        store.finish(source,'deferred',detail)
        return 2
    except Exception as exc:
        if new_intent and ui and not ui.claim_dispatched:
            store.cancel_unsent_intent(today)
        # Exceptions may contain URLs, so log only their class.
        detail='unexpected_'+type(exc).__name__
        event('failed',reason=detail)
        store.finish(source,'failed',detail)
        return 3
    finally:
        if ui:ui.close()
        cleanup_failed=False
        if isolated:
            try:
                if process:close_isolated(isolated,process,owned,runtime_port)
                else:isolated.close()
            except Exception as exc:
                event('isolated_cleanup_failed',error=type(exc).__name__)
                store.finish(source,'failed','isolated_cleanup_failed')
                cleanup_failed=True
        if guard:guard.close()
        if cleanup_failed:return 3

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',default='manual')
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    configure_logging()
    if not is_enabled():
        event('skip_paused_by_user')
        return 0
    handle=win32event.CreateMutex(None,False,MUTEX)
    locked=False
    store=None
    try:
        result=win32event.WaitForSingleObject(handle,0)
        if result not in (win32event.WAIT_OBJECT_0,win32event.WAIT_ABANDONED):
            event('skip_other_instance_running')
            return 0
        locked=True
        store=Store()
        return run(store,args.source,args.verify_only)
    finally:
        if store:store.close()
        if locked:win32event.ReleaseMutex(handle)
        win32api.CloseHandle(handle)

if __name__=='__main__':
    raise SystemExit(main())
