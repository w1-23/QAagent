"""Observe public Tavotto playground UI only; never download implementation code."""
from datetime import datetime, timezone
import json
from pathlib import Path
from browser_driver import Browser

out = Path(__file__).parent / 'results' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-reference-ui')
out.mkdir(exist_ok=False)
with Browser() as browser:
    browser.navigate('https://www.tavotto.com/try/?lang=zh')
    browser.wait('document.body && document.body.innerText.length > 80', seconds=25)
    browser.evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.innerText.trim()==="开始体验").click()')
    try:
        browser.wait('document.body.innerText.includes("图内元素") || document.body.innerText.includes("失败") || document.body.innerText.includes("Failed")', seconds=110)
    except TimeoutError:
        pass
    if browser.evaluate('document.body.innerText.includes("图内元素")'):
        browser.evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent.includes("跳过引导"))?.click()')
        for name, label in [('title', '标题 Reaction kinetics'), ('line', '曲线 Catalyst A'), ('legend', '图例')]:
            found = browser.evaluate('(()=>{const matches=Array.from(document.querySelectorAll("span,button,div")).filter(e=>e.textContent.trim()===' + json.dumps(label) + ');const e=matches[matches.length-1];if(!e)return false;(e.closest("button,[role=treeitem]")||e).click();return true;})()')
            (out / (name + '-panel.txt')).write_text(browser.evaluate('document.body.innerText'), encoding='utf-8')
            (out / (name + '-controls.json')).write_text(json.dumps(browser.evaluate('Array.from(document.querySelectorAll("input,textarea,select,button")).map(e=>({tag:e.tagName,type:e.type,label:e.getAttribute("aria-label"),title:e.title,value:e.value,text:e.innerText})).slice(-100)'), ensure_ascii=False, indent=2), encoding='utf-8')
            browser.screenshot(out / (name + '-panel.png'))
    text = browser.evaluate('document.body.innerText')
    controls = browser.evaluate('Array.from(document.querySelectorAll("button,input,select,summary")).map(e=>({tag:e.tagName,text:e.innerText,aria:e.getAttribute("aria-label"),title:e.title,type:e.type})).slice(0,150)')
    (out / 'public-ui.json').write_text(json.dumps({'url': 'https://www.tavotto.com/try/?lang=zh', 'text': text, 'controls': controls}, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.screenshot(out / 'public-ui.png')
    print(text[:14000])
    print('Evidence:', out)
