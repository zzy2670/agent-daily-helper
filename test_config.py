"""Offline path and control-channel boundary tests; no real client calls."""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import quote
import runtime_config as config
import worker

class PathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='agent-daily-config-')
        self.root=Path(self.temp.name)
        self.exe=self.root/'应用目录'/'WorkBuddy.exe'
        self.exe.parent.mkdir()
        self.exe.touch()
        (self.exe.parent/'resources').mkdir()
        (self.exe.parent/'resources'/'app.asar').touch()
        self.patch=patch.object(config,'DATA',self.root/'state')
        self.patch.start()
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()
    def test_explicit_nondefault_unicode_path(self):
        with patch.dict(os.environ,{'AGENT_DAILY_APP':str(self.exe)}):
            self.assertEqual(config.find_executable(),self.exe.resolve())
    def test_invalid_explicit_path_does_not_fallback(self):
        with patch.dict(os.environ,{'AGENT_DAILY_APP':str(self.root/'missing.exe')}):
            with self.assertRaises(FileNotFoundError):config.find_executable()
    def test_explicit_environment_variables_are_expanded(self):
        with patch.dict(os.environ,{'AGENT_TEST_HOME':str(self.exe.parent),
                                    'AGENT_DAILY_APP':r'%AGENT_TEST_HOME%\WorkBuddy.exe'}):
            self.assertEqual(config.find_executable(),self.exe.resolve())
    def test_saved_path_with_bom(self):
        config.DATA.mkdir()
        (config.DATA/'app_path.txt').write_text(str(self.exe),encoding='utf-8-sig')
        with patch.dict(os.environ,{'AGENT_DAILY_APP':''}):
            self.assertEqual(config.find_executable(),self.exe.resolve())
    def test_control_record_has_no_secrets_and_validates(self):
        with patch.object(config,'CONTROL',config.DATA/'control.json'):
            config.write_control({'pid':123,'created':1.5,'port':38111})
            self.assertEqual(config.read_control(),{'pid':123,'created':1.5,'port':38111})
            config.CONTROL.write_text(json.dumps({'pid':123,'created':1.5,'port':-1}),encoding='utf-8')
            self.assertIsNone(config.read_control())
    def test_readonly_database_path_with_reserved_characters(self):
        db=self.root/'名字 #1%.sqlite3'
        with sqlite3.connect(db) as connection:
            connection.execute('create table item (value integer)')
            connection.execute('insert into item values (7)')
        connection.close()
        with closing(sqlite3.connect(config.sqlite_readonly_uri(db),uri=True)) as connection:
            self.assertEqual(connection.execute('select value from item').fetchone(),(7,))

class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='agent-daily-endpoint-')
        self.exe=Path(self.temp.name)/'WorkBuddy.exe'
        self.pid=8101;self.port=38119
        self.proc=SimpleNamespace(exe=lambda:str(self.exe),create_time=lambda:42.0)
        self.listener=SimpleNamespace(status=worker.psutil.CONN_LISTEN,
          laddr=SimpleNamespace(ip='127.0.0.1',port=self.port),pid=self.pid)
        p=(self.exe.parent/'resources/app.asar/renderer/index.html').as_posix()
        self.target={'type':'page','url':'file:///'+quote(p)+'?unrelated=redacted',
          'webSocketDebuggerUrl':f'ws://127.0.0.1:{self.port}/devtools/page/example'}
    def tearDown(self):self.temp.cleanup()
    def patches(self):
        return (patch.object(worker,'EXE',self.exe),
          patch.object(worker.psutil,'net_connections',return_value=[self.listener]),
          patch.object(worker.psutil,'Process',return_value=self.proc))
    def test_valid_loopback_owner_and_renderer(self):
        a,b,c=self.patches()
        with a,b,c,patch.object(worker,'targets',return_value=[self.target]) as read:
            result=worker.validate_endpoint(allowed_pids={self.pid},port=self.port)
            self.assertEqual(result['_port'],self.port)
            read.assert_called_once_with(self.port)
    def test_foreign_listener_rejected_before_http(self):
        a,b,c=self.patches()
        with a,b,c,patch.object(worker,'targets',side_effect=AssertionError('must not connect')):
            with self.assertRaises(worker.RetryLater):worker.validate_endpoint(allowed_pids={999},port=self.port)
    def test_nonloopback_websocket_rejected(self):
        self.target['webSocketDebuggerUrl']=f'ws://example.com:{self.port}/devtools/page/example'
        a,b,c=self.patches()
        with a,b,c,patch.object(worker,'targets',return_value=[self.target]):
            with self.assertRaises(worker.RetryLater):worker.validate_endpoint(allowed_pids={self.pid},port=self.port)
    def test_stale_marker_rejected_before_http(self):
        a,b,c=self.patches()
        with a,b,c,patch.object(worker,'read_control',return_value={'pid':self.pid,'created':1.0,'port':self.port}), \
             patch.object(worker,'targets',side_effect=AssertionError('must not connect')):
            with self.assertRaises(worker.RetryLater):worker.validate_endpoint()

if __name__=='__main__':unittest.main(verbosity=2)
