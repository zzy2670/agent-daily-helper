import sys, json, urllib.request, itertools, time
import websocket

LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def targets(port):
    with LOCAL_HTTP.open(f'http://127.0.0.1:{port}/json/list', timeout=3) as f:
        return json.load(f)

class CDP:
    def __init__(self, target):
        self.target = target
        self.ws = websocket.create_connection(target['webSocketDebuggerUrl'],
            timeout=10, suppress_origin=True, http_proxy_host=None,
            http_no_proxy=['127.0.0.1','localhost','::1'])
        self.ids = itertools.count(1)
    def call(self, method, params=None):
        ident = next(self.ids)
        self.ws.send(json.dumps({'id':ident, 'method':method, 'params':params or {}}))
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            self.ws.settimeout(max(.1,deadline-time.monotonic()))
            msg = json.loads(self.ws.recv())
            if msg.get('id') == ident:
                if 'error' in msg:
                    raise RuntimeError(msg['error'].get('message','CDP call failed'))
                return msg.get('result',{})
        raise TimeoutError('CDP response timeout')
    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', {'expression':expression,
            'returnByValue':True, 'awaitPromise':True})
        if result.get('exceptionDetails'):
            raise RuntimeError('Page evaluation failed')
        return result.get('result',{}).get('value')
    def close(self):
        self.ws.close()
