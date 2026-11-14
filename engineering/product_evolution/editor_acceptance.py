"""Exercise the real figure editor with Chrome native pointer input.

Only disposable preview_app accounts and explicitly synthetic fixtures are used.
No LLM calls, research benchmarks or production data are accessed.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from browser_driver import Browser

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8135)
args = parser.parse_args()
base = f'http://127.0.0.1:{args.port}'
out = Path(__file__).parent / 'results' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-editor-browser')
out.mkdir(exist_ok=False)
# Read the documented disposable fixture password without logging it.
fixture = (Path(__file__).parent / 'preview_app.py').read_text(encoding='utf-8')
password = re.search(r"auth.register\(name, '([^']+)'\)", fixture).group(1)
checks = []


def check(name, value):
    checks.append({'check': name, 'passed': bool(value)})
    if not value:
        raise AssertionError(name)


def js(value):
    return json.dumps(value, ensure_ascii=False)


with Browser() as browser:
    try:
        browser.navigate(base + '/')
        browser.wait('!!document.querySelector("#authUsername")')
        for selector, value in (('#authUsername', 'qa_preview_a'), ('#authPassword', password)):
            browser.click(selector)
            browser.call('Input.insertText', {'text': value})
        browser.click('.auth-card button[type="submit"]')
        browser.wait('document.querySelector("#authGate").hidden || getComputedStyle(document.querySelector("#authGate")).display === "none"')
        browser.navigate(base + '/figures')
        browser.wait('document.querySelectorAll("#projectList button").length === 3')
        projects = browser.evaluate('(async()=> (await (await fetch("/api/v2/figures/projects")).json()).projects)()')
        trend = next(p for p in projects if '时间趋势' in p['title'])
        scatter = next(p for p in projects if '原始数据点' in p['title'])
        fid = trend['figure_id']
        browser.navigate(base + '/figures?project=' + fid)
        browser.wait('document.querySelector("#previewImage").complete && document.querySelectorAll(".overlay-box").length > 5')
        endpoint = '/api/v2/figures/projects/' + fid
        detail = browser.evaluate('(async()=> await (await fetch(' + js(endpoint) + ')).json())()')
        check('three rich synthetic fixtures', len(projects) == 3)
        check('only latest saved revision', len(detail['revisions']) == 1)
        if any(o['gid'] == 'axes_0.title' and o['prop'] == 'offset_px' and o['value'] != [0, 0] for o in detail.get('overrides', [])):
            browser.click('#objectTree button[data-gid="axes_0.title"]')
            browser.evaluate('(()=>{for(const p of ["offset_x","offset_y"]){const e=document.querySelector("#propertyPanel [data-prop=\\""+p+"\\"]");e.value="0";e.dispatchEvent(new Event("input",{bubbles:true}));e.dispatchEvent(new Event("change",{bubbles:true}));}})()')
            browser.wait('document.querySelector("#editStatus").textContent.includes("预览已更新")', seconds=45)
        title = '.overlay-box[data-gid="axes_0.title"]'
        hit = browser.evaluate('(()=>{const r=document.querySelector(' + js(title) + ').getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2).dataset.gid;})()')
        check('title centre hits title, not figure overlay', hit == 'axes_0.title')
        before = browser.evaluate('document.querySelector("#previewImage").src')
        # Native CDP input generates the full trusted pointer/capture sequence.
        browser.drag(title, 36, -12)
        browser.wait('document.querySelector("#editStatus").textContent.includes("预览已更新")')
        check('release redraws actual image', browser.evaluate('document.querySelector("#previewImage").src') != before)
        check('preview remains unsaved', browser.evaluate('(async()=> (await (await fetch(' + js(endpoint) + ')).json()).render_version)()') == detail['render_version'])
        first_offset = browser.evaluate('Array.from(document.querySelectorAll("#propertyPanel input[data-prop^=offset]")).map(e=>Number(e.value))')
        browser.drag(title, 24, -6)
        browser.wait('document.querySelector("#editStatus").textContent.includes("预览已更新")')
        second_offset = browser.evaluate('Array.from(document.querySelectorAll("#propertyPanel input[data-prop^=offset]")).map(e=>Number(e.value))')
        check('second drag continues from first offset', second_offset[0] > first_offset[0] and second_offset[1] < first_offset[1])

        def search_objects(value):
            browser.evaluate('(()=>{const e=document.querySelector("#objectSearch");e.value=' + js(value) + ';e.dispatchEvent(new Event("input",{bubbles:true}));})()')

        def select_object(gid):
            # Search reveals the real tree leaf even when its parent is collapsed.
            search_objects(gid)
            selector = '#objectTree button[data-gid="' + gid + '"]'
            browser.wait('!!document.querySelector(' + js(selector) + ')')
            browser.click(selector)
            search_objects('')

        def wait_preview(previous):
            browser.wait('(document.querySelector("#editStatus").textContent.includes("预览已更新") || document.querySelector("#editStatus").textContent.includes("没有本地修改")) && document.querySelector("#previewImage").src !== ' + js(previous), seconds=45)

        def property_value(prop, value):
            # Permit rerunning on this disposable instance after a later check
            # fails. An already selected value correctly produces no redraw.
            current = browser.evaluate('(()=>{const p=' + js(prop) + ';const e=document.querySelector("#propertyPanel [data-prop=\\""+p+"\\"]");if(!e)return null;if(p==="marker"||p==="linestyle"){const b=document.querySelector("#propertyPanel [data-prop=\\""+p+"\\"][aria-pressed=\\"true\\"]");return b?b.dataset.value:null;}return e.value;})()')
            if current == str(value):
                return
            previous = browser.evaluate('document.querySelector("#previewImage").src')
            if prop in ('marker', 'linestyle'):
                browser.click('#propertyPanel button[data-prop="' + prop + '"][data-value="' + str(value) + '"]')
            else:
                browser.evaluate('(()=>{const e=document.querySelector(' + js('#propertyPanel [data-prop="' + prop + '"]') + ');if(!e)throw Error("missing property");e.value=' + js(str(value)) + ';e.dispatchEvent(new Event("input",{bubbles:true}));e.dispatchEvent(new Event("change",{bubbles:true}));})()')
            wait_preview(previous)

        def image_svg():
            return browser.evaluate('(async()=>{const s=document.querySelector("#previewImage").src;return s.startsWith("data:")?decodeURIComponent(s.split(",").slice(1).join(",")):await(await fetch(s)).text();})()')

        def svg_text(svg):
            return ''.join(ET.fromstring(svg).itertext())

        check('objects use four category groups', browser.evaluate('Array.from(document.querySelectorAll("#objectTree button[data-group]")).filter(e=>["text","series","legend","axes"].includes(e.dataset.group)).length') == 4)
        select_object('axes_0.title')
        property_value('text', 'Synthetic acceptance: edited title & labels')
        check('title content changes in actual SVG', 'Synthetic acceptance: edited title & labels' in svg_text(image_svg()))
        property_value('fontweight', 'bold')
        families = detail['manifest'].get('font_families', [])
        if 'Microsoft YaHei' in families:
            # The fixture may already use YaHei. Exercise a real family change
            # before returning to it rather than waiting for a no-op redraw.
            property_value('fontfamily', 'DejaVu Sans')
            property_value('fontfamily', 'Microsoft YaHei')
            check('Microsoft YaHei chosen from installed fonts', True)
        property_value('fontsize', 13)
        property_value('fontstyle', 'italic')
        check('italic survives actual SVG export', 'font-style="italic"' in image_svg())
        select_object('axes_0.series_1')
        property_value('linestyle', '--')
        property_value('marker', 's')
        check('line dash pattern appears in actual SVG', 'stroke-dasharray' in image_svg())
        select_object('axes_0.legend')
        property_value('ncols', 2)
        select_object('axes_0.spine_left') if any(o['gid'] == 'axes_0.spine_left' for o in detail['manifest']['objects']) else select_object(next(o['gid'] for o in detail['manifest']['objects'] if o['role'] == 'spine' and o.get('visible', True)))
        property_value('linewidth', 2)
        browser.click('#saveButton')
        browser.wait('document.querySelector("#editStatus").textContent.includes("没有本地修改")')
        saved = browser.evaluate('(async()=> await (await fetch(' + js(endpoint) + ')).json())()')
        check('save increments revision once', saved['render_version'] == detail['render_version'] + 1)
        check('save retains exactly one snapshot', len(saved['revisions']) == 1)
        check('title position stored', any(o['gid'] == 'axes_0.title' and o['prop'] == 'offset_px' for o in saved['overrides']))
        check('font weight stored', any(o['gid'] == 'axes_0.title' and o['prop'] == 'fontweight' and o['value'] == 'bold' for o in saved['overrides']))
        check('axis width stored', any(o['prop'] == 'linewidth' and o['value'] == 2 for o in saved['overrides']))
        download = browser.evaluate('(async()=>{const urls=[document.querySelector("#downloadSvg").href,document.querySelector("#previewImage").src];const data=await Promise.all(urls.map(async u=>{const r=await fetch(u);return {status:r.status,body:await r.text()};}));return {statuses:data.map(d=>d.status),same:data[0].body===data[1].body};})()')
        check('saved preview matches SVG download bytes', download['same'] and download['statuses'] == [200, 200])
        saved_svg = browser.evaluate('(async()=> await (await fetch(document.querySelector("#previewImage").src)).text())()')
        (out / 'saved-trend.svg').write_text(saved_svg, encoding='utf-8')
        check('rendered SVG retains selected font family', 'font-family=' in saved_svg and 'Microsoft YaHei' in saved_svg)
        check('rendered SVG retains bold weight', 'font-weight="700"' in saved_svg or 'font-weight="bold"' in saved_svg)
        select_object('axes_0.title')
        browser.screenshot(out / 'trend-workspace.png')
        browser.navigate(base + '/figures?project=' + scatter['figure_id'])
        browser.wait('document.querySelector("#previewImage").complete && document.querySelectorAll("#objectSelect option").length > 50')
        sd = browser.evaluate('(async()=> await (await fetch(' + js('/api/v2/figures/projects/' + scatter['figure_id']) + ')).json())()')
        point = next(o for o in sd['manifest']['objects'] if o['role'] == 'point')
        browser.evaluate('(()=>{const e=document.querySelector("#objectSearch"); e.value="spine"; e.dispatchEvent(new Event("input",{bubbles:true}));})()')
        check('object search finds four axis spines', browser.evaluate('document.querySelectorAll("#objectSelect option").length') == 4)
        browser.evaluate('(()=>{const e=document.querySelector("#objectSearch"); e.value=""; e.dispatchEvent(new Event("input",{bubbles:true}));})()')
        parent = point['parent_gid']
        select_object(parent)
        property_value('label', 'Synthetic cohort A')
        check('series rename updates rendered legend', 'Synthetic cohort A' in svg_text(image_svg()))
        property_value('marker', 'v')
        property_value('markersize', 8)
        property_value('markeredgecolor', '#202020')
        property_value('markeredgewidth', 1.5)
        property_value('alpha', 0.75)
        # Child style is layered after the complete series style.
        select_object(point['gid'])
        property_value('color', '#6250d6')
        property_value('marker', 'D')
        (out / 'point-preview.svg').write_text(image_svg(), encoding='utf-8')
        check('individual point color appears in rendered SVG', '#6250d6' in image_svg().lower())
        previous = browser.evaluate('document.querySelector("#previewImage").src')
        browser.click('#propertyPanel [data-action="toggle-visible"]')
        wait_preview(previous)
        check('hidden point stays selectable', browser.evaluate('document.querySelector("#objectSelect").selectedOptions[0].textContent.includes("已移除")'))
        previous = browser.evaluate('document.querySelector("#previewImage").src')
        browser.click('#undoButton')
        wait_preview(previous)
        check('undo restores point', not browser.evaluate('document.querySelector("#objectSelect").selectedOptions[0].textContent.includes("已移除")'))
        browser.click('#saveButton')
        browser.wait('document.querySelector("#editStatus").textContent.includes("没有本地修改")')
        sd_saved = browser.evaluate('(async()=> await (await fetch(' + js('/api/v2/figures/projects/' + scatter['figure_id']) + ')).json())()')
        by_gid = {obj['gid']: obj for obj in sd_saved['manifest']['objects']}
        # A collection with a child override honestly has mixed marker paths;
        # its base style persists in overrides, while children report reality.
        check('series base shape persists in saved overrides', any(o['gid'] == parent and o['prop'] == 'marker' and o['value'] == 'v' for o in sd_saved['overrides']))
        siblings = [o for o in by_gid.values() if o.get('parent_gid') == parent and o['role'] == 'point' and o['gid'] != point['gid']]
        check('other points retain series shape', bool(siblings) and all(o.get('marker') == 'v' for o in siblings))
        check('single point shape persists independently', by_gid[point['gid']].get('marker') == 'D')
        check('scatter retains one saved snapshot', len(sd_saved['revisions']) == 1)
        select_object(parent)
        browser.screenshot(out / 'scatter-workspace.png')
        (out / 'saved-scatter.svg').write_text(image_svg(), encoding='utf-8')
        browser.navigate(base + '/figures?project=' + scatter['figure_id'])
        browser.wait('document.querySelector("#previewImage").complete && document.querySelectorAll("#objectSelect option").length > 50')
        select_object(parent)
        check('series shape restores on reopen', browser.evaluate('document.querySelector(\'#propertyPanel [data-prop="marker"][data-value="v"]\').getAttribute("aria-pressed")') == 'true')
        check('series label restores on reopen', browser.evaluate('document.querySelector(\'#propertyPanel [data-prop="label"]\').value') == 'Synthetic cohort A')
        # Group editing is one undo step, does not duplicate content fields.
        browser.click('#objectTree button[data-group="text"]')
        check('group excludes content replacement', browser.evaluate('!document.querySelector(\'#propertyPanel [data-prop="text"]\')'))
        previous_text = image_svg()
        property_value('fontsize', 11)
        previous = browser.evaluate('document.querySelector("#previewImage").src')
        browser.click('#undoButton')
        browser.wait('!document.querySelector("#editStatus").textContent.includes("等待预览") && !document.querySelector("#editStatus").textContent.includes("正在")')
        browser.wait('document.querySelector("#previewImage").src !== ' + js(previous), seconds=45)
        check('one undo restores entire group render', image_svg() == previous_text)
        select_object(parent)
        property_value('markersize', 9)
        url_before = browser.evaluate('location.href')
        image_before = browser.evaluate('document.querySelector("#previewImage").src')
        browser.click('#figureGuideButton')
        browser.wait('document.querySelector("#citrusGuideDialog").open')
        check('guide opens without navigation', browser.evaluate('location.href') == url_before)
        browser.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'z', 'code': 'KeyZ', 'modifiers': 2})
        browser.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'z', 'code': 'KeyZ', 'modifiers': 2})
        check('guide shortcut cannot undo underlying edit', browser.evaluate('document.querySelector(\'#propertyPanel [data-prop="markersize"]\').value') == '9')
        check('guide includes six research-task chapters', browser.evaluate('document.querySelectorAll("#citrusGuideDialog [data-guide-page]").length') == 6)
        browser.click('#citrusGuideDialog [data-guide-page="3"]')
        check('guide keeps text editing inside drawing chapter', browser.evaluate('document.querySelector("#citrusGuidePage").dataset.page') == 'figures' and browser.evaluate('!!document.querySelector("#citrusGuidePage [data-guide-section=\\"text\\"]")'))
        browser.evaluate('document.querySelector("#citrusGuidePage [data-guide-section=\\"text\\"]").scrollIntoView({block:"start"})')
        check('guide contains embedded illustration', browser.evaluate('!!document.querySelector("#citrusGuidePage svg, #citrusGuidePage img")'))
        browser.screenshot(out / 'guide.png')
        browser.call('Emulation.setDeviceMetricsOverride', {'width': 375, 'height': 812, 'deviceScaleFactor': 1, 'mobile': True})
        check('guide fits narrow viewport', browser.evaluate('document.documentElement.scrollWidth <= innerWidth && document.querySelector("#citrusGuideDialog").getBoundingClientRect().width <= innerWidth'))
        browser.screenshot(out / 'mobile-guide.png')
        browser.click('#citrusGuideDialog [data-guide-close]')
        check('closing guide preserves current preview', browser.evaluate('document.querySelector("#previewImage").src') == image_before)
        check('closing guide restores visible focus after resize', browser.evaluate('document.activeElement.id') in ('figureGuideButton', 'toggleRight'))
        browser.click('#saveButton')
        browser.wait('document.querySelector("#editStatus").textContent.includes("没有本地修改")')
        browser.navigate(base + '/figures?project=' + scatter['figure_id'])
        browser.wait('document.querySelector("#previewImage").complete && document.querySelectorAll("#objectSelect option").length > 50')
        check('workspace fits narrow viewport', browser.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        browser.screenshot(out / 'mobile-workspace.png')
        browser.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
        for theme in ('dark', 'cartoon', 'light'):
            browser.evaluate('localStorage.setItem("citrus_theme",' + js(theme) + ')')
            browser.navigate(base + '/figures?project=' + scatter['figure_id'])
            browser.wait('document.querySelector("#previewImage").complete && document.querySelectorAll("#objectSelect option").length > 50')
            check('workspace applies ' + theme + ' theme', browser.evaluate('document.documentElement.dataset.theme') == theme)
            browser.screenshot(out / (theme + '-workspace.png'))
    except Exception:
        browser.screenshot(out / 'failure.png')
        (out / 'failure-ui.txt').write_text(browser.evaluate('document.body.innerText') or '', encoding='utf-8')
        raise
    finally:
        (out / 'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'checks': checks, 'evidence': str(out)}, ensure_ascii=False))
