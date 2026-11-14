"""Small CDP driver for isolated, native-mouse browser acceptance.

Starts an owned Chrome with an empty temporary profile. It never attaches to a
user's browser. The application itself must be launched via the preview tool.
"""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
import websocket


class Browser:
    def __enter__(self):
        self.profile = tempfile.TemporaryDirectory(prefix='citrus-browser-', ignore_cleanup_errors=True)
        self.process = subprocess.Popen([
            'C:/Program Files/Google/Chrome/Application/chrome.exe',
            '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
            '--remote-debugging-port=0', '--window-size=1440,1000',
            '--user-data-dir=' + self.profile.name, 'about:blank',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = Path(self.profile.name) / 'DevToolsActivePort'
            deadline = time.monotonic() + 15
            port = None
            while port is None:
                if self.process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Owned Chrome did not publish a readable debugging port')
                try:
                    lines = port_file.read_text().splitlines()
                    if lines and lines[0].isdigit():
                        port = lines[0]
                except (FileNotFoundError, PermissionError):
                    # Chrome can briefly hold this new file open while writing
                    # it on Windows. Retry only our owned temporary profile.
                    pass
                if port is None:
                    time.sleep(.1)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:' + port + '/json/list', timeout=10) as response:
                targets = json.load(response)
            target = next(item for item in targets if item['type'] == 'page')
            self.socket = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=25, suppress_origin=True)
            self.sequence = 0
            self.call('Page.enable')
            self.call('Runtime.enable')
            self.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if hasattr(self, 'socket'):
            self.socket.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.profile.cleanup()

    def call(self, method, params=None):
        self.sequence += 1
        self.socket.send(json.dumps({'id': self.sequence, 'method': method, 'params': params or {}}))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            message = json.loads(self.socket.recv())
            if message.get('id') == self.sequence:
                if 'error' in message:
                    raise RuntimeError(message['error'])
                return message.get('result', {})
        raise TimeoutError(method)

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})
        if result.get('exceptionDetails'):
            raise RuntimeError(result['exceptionDetails'])
        return result.get('result', {}).get('value')

    def wait(self, expression, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = self.evaluate(expression)
            if value:
                return value
            time.sleep(.15)
        raise TimeoutError(expression[:300] + ('…' if len(expression) > 300 else ''))

    def navigate(self, url):
        self.call('Page.navigate', {'url': url})
        self.wait('document.readyState === "complete"')

    def click(self, selector):
        rect = self.evaluate('(()=>{const e=document.querySelector(' + json.dumps(selector) + '); if(!e)throw Error("Missing control");e.scrollIntoView({block:"center",inline:"nearest"});const r=e.getBoundingClientRect();if(!r.width||!r.height)throw Error("Hidden control");return {x:r.x+r.width/2,y:r.y+r.height/2};})()')
        self.call('Input.dispatchMouseEvent', {'type': 'mousePressed', **rect, 'button': 'left', 'clickCount': 1})
        self.call('Input.dispatchMouseEvent', {'type': 'mouseReleased', **rect, 'button': 'left', 'clickCount': 1})

    def drag(self, selector, dx, dy):
        rect = self.evaluate('(()=>{const r=document.querySelector(' + json.dumps(selector) + ').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()')
        self.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', **rect})
        self.call('Input.dispatchMouseEvent', {'type': 'mousePressed', **rect, 'button': 'left', 'buttons': 1, 'clickCount': 1})
        for step in range(1, 9):
            self.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': rect['x'] + dx * step / 8, 'y': rect['y'] + dy * step / 8, 'button': 'left', 'buttons': 1})
        self.call('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': rect['x'] + dx, 'y': rect['y'] + dy, 'button': 'left', 'buttons': 0, 'clickCount': 1})

    def screenshot(self, path):
        result = self.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
        Path(path).write_bytes(base64.b64decode(result['data']))
