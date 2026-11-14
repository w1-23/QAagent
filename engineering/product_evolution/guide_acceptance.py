"""Real in-page guide checks using disposable preview accounts only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from browser_driver import Browser

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8135)
args = parser.parse_args()
base = f'http://127.0.0.1:{args.port}'
out = Path(__file__).parent / 'results' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-guide-browser')
out.mkdir(exist_ok=False)
fixture = (Path(__file__).parent / 'preview_app.py').read_text(encoding='utf-8')
password = re.search(r"auth.register\(name, '([^']+)'\)", fixture).group(1)
checks = []


def check(name, value):
    checks.append({'check': name, 'passed': bool(value), 'source': 'real_local_preview'})
    if not value:
        raise AssertionError(name)


with Browser() as browser:
    try:
        browser.call('Network.enable')
        browser.call('Network.setBlockedURLs', {'urls': ['https://*']})
        browser.navigate(base + '/')
        browser.wait('!!document.querySelector("#authUsername")')
        for selector, value in (('#authUsername', 'qa_preview_a'), ('#authPassword', password)):
            browser.click(selector)
            browser.call('Input.insertText', {'text': value})
        browser.click('.auth-card button[type="submit"]')
        browser.wait('document.querySelector("#authGate").hidden || getComputedStyle(document.querySelector("#authGate")).display === "none"')
        before = browser.evaluate('location.href')
        browser.click('#guideRailBtn')
        browser.wait('document.querySelector("#citrusGuideDialog").open')
        check('chat opens guide in current page', browser.evaluate('location.href') == before)
        check('guide has no external navigation links', browser.evaluate('!document.querySelector("#citrusGuideDialog a[href]")'))
        count = browser.evaluate('document.querySelectorAll("#citrusGuideDialog [data-guide-page]").length')
        check('six research-task chapters', count == 6)
        pages = []
        for index in range(count):
            browser.click(f'#citrusGuideDialog [data-guide-page="{index}"]')
            page = browser.evaluate('document.querySelector("#citrusGuidePage").dataset.page')
            pages.append(page)
            check(page + ' has illustration', browser.evaluate('!!document.querySelector("#citrusGuidePage svg, #citrusGuidePage img")'))
        check('research chapters in task order', pages == ['research', 'files', 'data', 'figures', 'results', 'account'])
        browser.click('#citrusGuideDialog [data-guide-page="3"]')
        check('drawing is one chapter with five sections', browser.evaluate('Array.from(document.querySelectorAll("#citrusGuidePage [data-guide-section]"), node => node.dataset.guideSection)') == ['create', 'objects', 'text', 'series', 'save'])
        check('drawing sections preserve original illustrations', browser.evaluate('document.querySelectorAll("#citrusGuidePage svg").length') == 5)
        browser.screenshot(out / 'guide-drawing.png')
        browser.evaluate('document.querySelector("#citrusGuidePage [data-guide-section=\\"text\\"]").scrollIntoView({block:"start"})')
        check('text is a drawing subsection with its original illustration', browser.evaluate('!!document.querySelector("#citrusGuidePage [data-guide-section=\\"text\\"] svg") && document.querySelector("#citrusGuidePage").dataset.page === "figures"'))
        browser.screenshot(out / 'guide-text.png')
        browser.call('Emulation.setDeviceMetricsOverride', {'width': 375, 'height': 812, 'deviceScaleFactor': 1, 'mobile': True})
        browser.evaluate('document.querySelector("#citrusGuidePage [data-guide-section=\\"text\\"]").scrollIntoView({block:"start"})')
        check('guide fits mobile width', browser.evaluate('document.documentElement.scrollWidth <= innerWidth && document.querySelector("#citrusGuideDialog").getBoundingClientRect().width <= innerWidth'))
        browser.screenshot(out / 'guide-mobile.png')
        browser.evaluate('document.querySelector("#citrusGuideDialog [data-guide-close]").focus()')
        browser.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Tab', 'code': 'Tab', 'modifiers': 8})
        browser.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Tab', 'code': 'Tab', 'modifiers': 8})
        check('shift Tab stays inside the native guide dialog', browser.evaluate('document.activeElement.hasAttribute("data-guide-next")'))
        browser.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Tab', 'code': 'Tab'})
        browser.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Tab', 'code': 'Tab'})
        check('Tab wraps back to the guide close button', browser.evaluate('document.activeElement.hasAttribute("data-guide-close")'))
        browser.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Escape', 'code': 'Escape'})
        browser.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Escape', 'code': 'Escape'})
        check('escape closes guide', not browser.evaluate('document.querySelector("#citrusGuideDialog").open'))
        check('guide restores chat opener focus', browser.evaluate('document.activeElement.id') == 'guideRailBtn')
        check('guide leaves chat URL unchanged', browser.evaluate('location.href') == before)
        browser.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
        browser.navigate(base + '/figures')
        browser.wait('!!document.querySelector("#figureGuideButton") && typeof CitrusGuide === "object"')
        workbench_url = browser.evaluate('location.href')
        browser.click('#figureGuideButton')
        browser.wait('document.querySelector("#citrusGuideDialog").open')
        check('workbench opens the single drawing chapter', browser.evaluate('document.querySelector("#citrusGuidePage").dataset.page') == 'figures')
        check('workbench guide uses six chapters', browser.evaluate('document.querySelectorAll("#citrusGuideDialog [data-guide-page]").length') == 6)
        browser.evaluate('document.querySelector("#citrusGuidePage [data-guide-section=\\"text\\"]").scrollIntoView({block:"start"})')
        browser.screenshot(out / 'workbench-guide-text.png')
        browser.call('Emulation.setDeviceMetricsOverride', {'width': 375, 'height': 812, 'deviceScaleFactor': 1, 'mobile': True})
        browser.evaluate('document.querySelector("#citrusGuidePage [data-guide-section=\\"text\\"]").scrollIntoView({block:"start"})')
        check('workbench guide fits mobile width', browser.evaluate('document.documentElement.scrollWidth <= innerWidth && document.querySelector("#citrusGuideDialog").getBoundingClientRect().width <= innerWidth'))
        browser.screenshot(out / 'workbench-guide-mobile.png')
        browser.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Escape', 'code': 'Escape'})
        browser.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Escape', 'code': 'Escape'})
        check('workbench Escape closes guide', not browser.evaluate('document.querySelector("#citrusGuideDialog").open'))
        check('workbench close restores visible opener or responsive fallback', browser.evaluate('(()=>{const e=document.activeElement,r=e.getBoundingClientRect();return ["figureGuideButton","toggleRight"].includes(e.id)&&r.width>0&&r.height>0;})()'))
        check('workbench guide leaves URL unchanged', browser.evaluate('location.href') == workbench_url)
    except Exception:
        browser.screenshot(out / 'failure.png')
        (out / 'failure-ui.txt').write_text(browser.evaluate('document.body.innerText') or '', encoding='utf-8')
        raise
    finally:
        (out / 'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'checks': checks, 'evidence': str(out)}, ensure_ascii=False))
