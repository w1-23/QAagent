from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '绘图产物'
SKILL = Path(r'C:\Users\Administrator\.codex\skills\nature-figure\scripts')
SOURCE = ROOT / '绘图脚本/绘图_Fig4_最终双面板.py'
SNAP = json.loads((ROOT / '数据/Fig4_冻结绘图输入.json').read_text(encoding='utf-8'))
STYLE = json.loads((ROOT / '数据/绘图样式与标签.json').read_text(encoding='utf-8'))


def run(script, args, json_file=None):
    p = subprocess.run([sys.executable, str(SKILL/script), *map(str,args)],
                       encoding='utf-8', capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stdout + p.stderr)
    if json_file:
        result = json.loads(p.stdout)
        json_file.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    return p.stdout


def main():
    preflight = run('validate_figure.py', [SOURCE, '--json'], OUT / '源码预检.json')
    assert preflight['summary']['ready'] and preflight['summary']['counts']['FAIL'] == 0
    records = []
    for name, expected_h in [('图4a_旧架构联网混入风险',63), ('图4b_新架构结构核验',48)]:
        pdf = OUT / (name+'.pdf')
        glyph = run('audit_pdf_text.py',[pdf,'--min-pt','8','--json'],OUT/(name+'.font-audit.json'))
        run('audit_figure_collisions.py',[pdf,'--json-out',OUT/(name+'.collision-audit.json')])
        collision = json.loads((OUT/(name+'.collision-audit.json')).read_text(encoding='utf-8'))
        assert collision['verdict'] == 'PASS'
        with fitz.open(pdf) as doc:
            page = doc[0]
            width = page.rect.width*25.4/72
            height = page.rect.height*25.4/72
            assert abs(width-174)<.01 and abs(height-expected_h)<.01
            assert not page.get_images(), 'Quantitative vector PDFs must have no raster images'
            fonts = page.get_fonts()
            assert all('Arial' in f[3] for f in fonts)
            assert all(len(doc.extract_font(f[0])[3])>0 for f in fonts), 'Font must be embedded'
            actual = [span['size'] for b in page.get_text('dict')['blocks'] if 'lines' in b
                      for line in b['lines'] for span in line['spans']]
            assert min(actual) >= 8-.001
        tree = ET.parse(OUT/(name+'.svg')).getroot()
        ns = '{http://www.w3.org/2000/svg}'
        texts = [''.join(t.itertext()) for t in tree.iter(ns+'text')]
        assert texts and not list(tree.iter(ns+'image'))
        forbidden = ['Fig.', 'Source-boundary compliance', 'Citation integrity',
                     'Difference vs', 'Old architecture', 'v11', '2026', '100/144',
                     '99.78', '99.96', '85.07', 'Structural traceability']
        assert all(not any(s in t for s in forbidden) for t in texts)
        assert not any(t in ['a','b','(a)','(b)'] for t in texts)
        hexes = set(re.findall(r'#[a-fA-F0-9]{6}',(OUT/(name+'.svg')).read_text(encoding='utf-8')))
        allowed = {v.lower() for v in STYLE['colors'].values()} | {'#ffffff'}
        assert {h.lower() for h in hexes} <= allowed
        if name.startswith('图4a'):
            for a in SNAP['arms']:
                assert f"{a['events']}/{a['verdicts']}" in texts
                assert f"{a['percent']:.1f}%" in texts
                assert f"{a['arm'].replace(chr(39),'′')} · {a['label']}" in texts
        else:
            for t in ['Local section: 0','Web section: 820','0/144 answers','47/48 answers',
                      '1,649/1,649','0/1,649','Web citations (occurrences)','Local citations (markers)']:
                assert t in texts, t
        alignment=json.loads((OUT/(name+'.alignment.json')).read_text(encoding='utf-8'))
        assert alignment['verdict'] == ('NOT APPLICABLE' if name.startswith('图4a') else 'PASS')
        with Image.open(OUT/(name+'.tiff')) as im:
            assert im.info['dpi'] == (600,600)
            assert im.mode == 'RGB'
            raster=dict(pixels=im.size,dpi=[float(d) for d in im.info['dpi']],mode=im.mode)
        eps=(OUT/(name+'.eps')).read_text(encoding='latin-1')
        assert '/FontType 42' in eps and '/sfnts[' in eps
        records.append(dict(panel=name,width_mm=width,height_mm=height,
                            minimum_font_pt=min(actual),svg_live_text_objects=len(texts),
                            vector_no_images=True,embedded_arial=True,palette=sorted(hexes),
                            collision=collision['verdict'],alignment=alignment['verdict'],raster=raster,
                            pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest()))
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in SNAP['source_sha256'].items())
    assert unchanged
    result=dict(panels=records,source_hashes_unchanged=unchanged,
                source_preflight_warning='174 mm is the user/HA double-column contract; the skill static default is 89/183 mm.',
                unresolved_data_conflict=SNAP['unresolved_conflict'],
                final_status='Visual/technical QA passed; frozen D nominal denominator explicitly caveated in legend.')
    (OUT/'QA_最终汇总.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
