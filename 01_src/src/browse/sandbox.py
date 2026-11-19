"""Fixed Chromium controller for the networkless Browse image."""
from __future__ import annotations

import base64
import json
import os
import socket
import sys
import uuid

sys.path.insert(0, '/browser-python')
from playwright.sync_api import Error, TimeoutError, sync_playwright


MAX_TABS = 3


def send(kind, **data):
    print(json.dumps({'kind': kind, **data}, ensure_ascii=True, separators=(',', ':')), flush=True)


def read():
    line = sys.stdin.buffer.readline(12 * 1024 * 1024 + 1)
    if not line or len(line) > 12 * 1024 * 1024 or not line.endswith(b'\n'):
        raise EOFError
    return json.loads(line)


def relay(route):
    request = route.request
    if request.method not in {'GET', 'HEAD'}:
        route.abort()
        return
    send('request', url=request.url, method=request.method)
    answer = read()
    if answer.get('ok') is not True:
        route.abort()
        return
    route.fulfill(status=answer['status'], headers=answer['headers'],
                  body=base64.b64decode(answer['body'], validate=True))


_SNAPSHOT = '''() => {
    const body = document.querySelector('main') || document.body;
    const text = body ? body.innerText.slice(0, 4800) : '';
    const nodes = Array.from(document.querySelectorAll('a[href],button,summary,[role=button]'));
    const elements = [];
    for (const node of nodes.slice(0, 1500)) {
        if (elements.length >= 24) break;
        const rect = node.getBoundingClientRect();
        if (!rect.width || !rect.height || rect.bottom <= 0 || rect.top >= innerHeight) continue;
        if (node.disabled || node.closest('form') || getComputedStyle(node).visibility !== 'visible') continue;
        const label = (node.innerText || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
        if (!label) continue;
        elements.push({node, label: label.slice(0, 140), role: node.localName,
                       href: node.localName === 'a' ? node.href.slice(0, 1000) : ''});
    }
    return {text, elements, scroll_y: Math.round(scrollY)};
}'''


class Browser:
    def __init__(self, page):
        self.page = page
        self.references = {}

    def clear_references(self):
        for element in self.references.values():
            element.dispose()
        self.references.clear()

    def snapshot(self):
        self.clear_references()
        result = self.page.evaluate_handle(_SNAPSHOT)
        try:
            array = result.get_property('elements')
            entries = []
            try:
                for index, handle in array.get_properties().items():
                    if not index.isdecimal():
                        handle.dispose()
                        continue
                    try:
                        node = handle.get_property('node').as_element()
                        ref = uuid.uuid4().hex[:12]
                        self.references[ref] = node
                        data = handle.evaluate('entry => ({label: entry.label, role: entry.role, href: entry.href})')
                        entries.append({'ref': ref, **data})
                    finally:
                        handle.dispose()
            finally:
                array.dispose()
            observed = result.evaluate('view => ({text: view.text, scroll_y: view.scroll_y})')
        finally:
            result.dispose()
        return {'url': self.page.url[:2048], 'title': self.page.title()[:180], 'elements': entries, **observed}

    def action(self, command):
        action = command['action']
        if action == 'open':
            response = self.page.goto(command['url'], wait_until='domcontentloaded', timeout=10000)
            if not response or response.status >= 400:
                return {'error': 'browse_page_unavailable'}
        elif action == 'scroll':
            self.page.evaluate('delta => window.scrollBy(0, delta)', command['delta'])
        elif action in {'click', 'download'}:
            element = self.references.get(command['ref'])
            if element is None or not element.evaluate('node => node.isConnected'):
                return {'error': 'browse_stale_element'}
            if action == 'download':
                href = element.evaluate("node => node.localName === 'a' ? node.href : ''")
                return {'href': href[:4096]} if href else {'error': 'browse_expected_download_link'}
            try:
                element.click(timeout=10000)
            except TimeoutError:
                self.clear_references()
                return {'error': 'browse_click_timeout'}
        elif action != 'observe':
            return {'error': 'browse_invalid_action'}
        return self.snapshot()


class Tabs:
    def __init__(self, context):
        self.context = context
        self.pages = {}
        self.active = self.add()

    def add(self):
        page = self.context.new_page()
        page.on('popup', lambda popup: popup.close())
        tab_id = 't-' + uuid.uuid4().hex[:12]
        self.pages[tab_id] = Browser(page)
        return tab_id

    def remove(self, tab_id):
        tab = self.pages[tab_id]
        tab.page.close()
        del self.pages[tab_id]

    def list(self):
        return [{'tab_id': key, 'url': tab.page.url[:2048], 'active': key == self.active}
                for key, tab in self.pages.items()]

    def action(self, command):
        action = command['action']
        tab_id = command.get('tab_id') or self.active
        if action == 'list_tabs':
            return {'tab_id': self.active, 'tabs': self.list()}
        if action == 'new_tab':
            if len(self.pages) >= MAX_TABS:
                return {'error': 'browse_tab_limit'}
            tab_id = self.add()
            try:
                result = self.pages[tab_id].action({**command, 'action': 'open'})
                if 'error' in result:
                    self.remove(tab_id)
                    return result
            except Error:
                self.remove(tab_id)
                raise
            self.active = tab_id
            return {**result, 'tab_id': tab_id}
        tab = self.pages.get(tab_id)
        if tab is None:
            return {'error': 'browse_tab_not_found'}
        if action == 'close_tab':
            if len(self.pages) == 1:
                return {'error': 'browse_last_tab'}
            self.remove(tab_id)
            if self.active == tab_id:
                self.active = next(reversed(self.pages))
            return {'tab_id': self.active, 'closed_tab_id': tab_id, 'tabs': self.list()}
        try:
            if action == 'switch_tab':
                result = tab.snapshot()
                self.active = tab_id
            else:
                result = tab.action(command)
        except Error:
            tab.clear_references()
            raise
        return {**result, 'tab_id': tab_id}


def main():
    if os.readlink('/proc/self/ns/net') == sys.argv[1] or {name for _, name in socket.if_nameindex()} - {'lo'}:
        raise RuntimeError('browse_network_namespace_required')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=12000,
            args=['--disable-background-networking', '--disable-quic', '--disable-extensions'])
        context = browser.new_context(accept_downloads=False, service_workers='block',
                                      viewport={'width': 1200, 'height': 850})
        context.route('**/*', relay)
        context.route_web_socket('**/*', lambda connection: connection.close())
        controller = Tabs(context)
        send('ready', engine=browser.version)
        try:
            while True:
                command = read()
                if command['action'] == 'close':
                    return
                try:
                    send('result', **controller.action(command))
                except Error:
                    send('result', error='browse_page_action_failed')
        except EOFError:
            return
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    main()
