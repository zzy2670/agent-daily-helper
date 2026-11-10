"""Offline regression tests. No real WorkBuddy reward is submitted."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import urllib.request
import os
import shutil
import worker
from cdp_client import CDP
from isolated_desktop import IsolatedDesktop
from runtime_config import choose_port

class Guard:
    def check(self):pass
    def close(self):pass

class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='workbuddy-state-test-')
        self.store=worker.Store(Path(self.temp.name))
        self.exe_patch=patch.object(worker,'EXE',Path(self.temp.name)/'WorkBuddy.exe')
        self.exe_patch.start()
    def tearDown(self):
        self.exe_patch.stop();self.store.close();self.temp.cleanup()
    def fake_run(self, page, **kwargs):
        with patch.object(worker,'app_processes',return_value=[object()]), \
             patch.object(worker,'validate_endpoint',return_value={'_owner_pid':-1,'_owner_created':0}), \
             patch.object(worker,'close_identity',return_value={'pid':-1,'created':0,'family':{}}), \
             patch.object(worker,'close_existing'), \
             patch.object(worker,'FocusGuard',return_value=Guard()), \
             patch.object(worker,'PageUI',return_value=page):
            return worker.run(self.store,source='offline_test',**kwargs)
    def test_singapore_day_at_utc_boundary(self):
        self.assertEqual(worker.day_key(datetime(2026,11,10,16,1,tzinfo=timezone.utc)),'2026-11-11')
    def test_success_skips_without_touching_application(self):
        self.store.success(worker.day_key())
        with patch.object(worker,'app_processes',side_effect=AssertionError('must not inspect app')):
            self.assertEqual(worker.run(self.store),0)
    def test_pending_never_submits_second_claim(self):
        self.store.intent(worker.day_key())
        page=FakePage()
        self.assertEqual(self.fake_run(page),2)
        self.assertEqual(page.claim_clicks,0)
        self.assertEqual(self.store.get(worker.day_key())['status'],'pending')
    def test_previously_manual_claim_is_recorded(self):
        page=FakePage(claimed=True)
        self.assertEqual(self.fake_run(page),0)
        self.assertEqual(page.claim_clicks,0)
        self.assertEqual(self.store.get(worker.day_key())['status'],'success')
    def test_uncertain_pressed_response_remains_pending(self):
        page=FakePage(error_after_pressed=True)
        self.assertEqual(self.fake_run(page),3)
        self.assertEqual(self.store.get(worker.day_key())['status'],'pending')
        self.assertEqual(self.fake_run(page),2)
        self.assertEqual(page.claim_clicks,1)
    def test_unsubmitted_intent_can_be_retried(self):
        page=FakePage(error_before_pressed=True)
        self.assertEqual(self.fake_run(page),2)
        self.assertEqual(self.store.get(worker.day_key())['status'],'error')
        page=FakePage()
        self.assertEqual(self.fake_run(page),0)
        self.assertEqual(page.claim_clicks,1)
        self.assertEqual(self.store.get(worker.day_key())['status'],'success')
    def test_old_success_does_not_block_new_day(self):
        self.store.success('2026-01-01')
        page=FakePage()
        self.assertEqual(self.fake_run(page),0)
        self.assertEqual(page.claim_clicks,1)
    def test_cross_midnight_before_pressed_aborts(self):
        ui=object.__new__(worker.PageUI)
        ui.guard=Guard();ui.claim_dispatched=False
        ui.query=lambda _: {'x':10,'y':10,'label':'立即领取'}
        class Wire:
            def __init__(self):self.calls=[]
            def call(self,*args):self.calls.append(args)
        ui.cdp=Wire()
        with patch.object(worker,'day_key',side_effect=['2026-11-10','2026-11-11']):
            with self.assertRaises(worker.RetryLater):ui.click('claim','2026-11-10')
        self.assertEqual(ui.cdp.calls,[])

class FakePage:
    def __init__(self,claimed=False,error_before_pressed=False,error_after_pressed=False):
        self.claimed=claimed;self.claim_dispatched=False;self.claim_clicks=0
        self.error_before_pressed=error_before_pressed;self.error_after_pressed=error_after_pressed
    def wait(self,predicate,seconds):
        return {'claimed':self.claimed,'claim':{'x':1,'y':1},'profile':True,'loginRequired':False}
    def open_card(self,day):return self.wait(None,None)
    def query(self,kind):return {'x':1,'y':1}
    def click(self,kind,day):
        if self.error_before_pressed:raise worker.RetryLater('claim_obscured')
        self.claim_dispatched=True;self.claim_clicks+=1
        if self.error_after_pressed:raise ConnectionError('simulated lost response')
        self.claimed=True
    def close(self):pass

FIXTURE='''<!doctype html><meta charset="utf-8"><style>
body{margin:0;font:16px sans-serif}button{padding:12px}.wb-slot{display:block;width:280px;height:140px}
.marketing-modal,.unknown-modal{position:fixed;inset:0;background:white;z-index:999;padding:20px}
.sk-residue{position:fixed;inset:0;opacity:0;pointer-events:none;z-index:1000}
</style><button class="user-menu-trigger">测试用户</button>
<div class="wb-slot wb-slot--avatar-top-combo"></div>
<div class="marketing-modal" role="dialog"><h2>WorkBuddy 新版本发布</h2><p>限时活动</p><button aria-label="关闭">关闭</button></div>
<div class="unknown-modal" role="dialog" style="display:none"><h2>活动公告</h2><textarea>未保存的内容</textarea><button aria-label="关闭">关闭</button></div>
<div class="sk-residue"><button>重启</button></div><script>
window.claimCount=0;window.dismissCount=0;
const r=document.querySelector('.wb-slot').attachShadow({mode:'open'});
r.innerHTML='<div>Buddy加油站</div><button class="go">立即领取</button>';
r.querySelector('button').onclick=e=>{window.claimCount++;e.target.textContent='今日已领取'};
document.querySelector('.marketing-modal button').onclick=()=>{window.dismissCount++;document.querySelector('.marketing-modal').style.display='none'};
</script>'''

class RealRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='workbuddy-renderer-test-')
        cls.folder=Path(cls.temp.name);fixture=cls.folder/'fixture.html';fixture.write_text(FIXTURE,encoding='utf-8')
        candidates=[Path(os.environ.get(k,''))/'Google/Chrome/Application/chrome.exe' for k in ('ProgramFiles','ProgramFiles(x86)')]
        found=next((p for p in candidates if p.is_file()),None)
        if not found:
            found=shutil.which('chrome')
        if not found:raise unittest.SkipTest('Chrome 未安装；跳过真实渲染器测试')
        cls.desktop=IsolatedDesktop()
        chrome=Path(found)
        port=choose_port()
        cls.process=cls.desktop.launch(str(chrome),['--headless=new','--no-first-run',
          '--no-default-browser-check','--disable-extensions','--remote-debugging-address=127.0.0.1',
          f'--remote-debugging-port={port}','--user-data-dir='+str(cls.folder/'profile'),fixture.as_uri()])
        deadline=time.monotonic()+20;target=None
        while time.monotonic()<deadline:
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{port}/json/list',timeout=1) as f:pages=json.load(f)
                target=next((p for p in pages if p.get('type')=='page' and p.get('url')==fixture.as_uri()),None)
                if target:break
            except OSError:pass
            time.sleep(.2)
        if not target:raise RuntimeError('Fixture renderer did not start')
        cls.cdp=CDP(target)
        cls.ui=object.__new__(worker.PageUI);cls.ui.cdp=cls.cdp;cls.ui.guard=Guard();cls.ui.claim_dispatched=False
        cls.cdp.call('Emulation.setFocusEmulationEnabled',{'enabled':True})
    @classmethod
    def tearDownClass(cls):
        cls.cdp.close();cls.desktop.close()
        time.sleep(.3)
        cls.temp.cleanup()
    def test_virtual_pointer_closes_ad_and_claims_once(self):
        before=worker.native_context()
        self.assertIsNone(self.ui.query('claim'))  # Ad actually covers the control.
        self.assertEqual(self.ui.query('dismiss')['category'],'known_promotion')
        self.ui.dismiss_ads(worker.day_key())
        self.assertEqual(self.cdp.evaluate('window.dismissCount'),1)
        self.ui.click('claim',worker.day_key())
        self.assertTrue(self.ui.query('status')['claimed'])
        self.assertIsNone(self.ui.query('claim'))
        self.assertEqual(self.cdp.evaluate('window.claimCount'),1)
        after=worker.native_context()
        self.assertEqual(before,after)
    def test_unknown_editable_dialog_is_not_closed(self):
        self.cdp.evaluate("document.querySelector('.unknown-modal').style.display='block'")
        self.assertIsNone(self.ui.query('dismiss'))
        self.assertIsNone(self.ui.query('claim'))
        self.cdp.evaluate("document.querySelector('.unknown-modal').style.display='none'")

if __name__=='__main__':
    unittest.main(verbosity=2)
