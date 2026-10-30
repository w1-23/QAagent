"""User-reference finishing pass. All artwork is drawn in Python from frozen observations."""
from pathlib import Path
import json, os, sys

ROOT=Path(__file__).resolve().parent.parent
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.mpl-cache'))
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle, FancyBboxPatch
from PIL import Image
sys.path.insert(0,r'C:\Users\Administrator\.codex\skills\nature-figure\scripts')
from audit_panel_alignment import require_matplotlib_panel_alignment

SNAP=json.loads((ROOT/'数据/Fig4_冻结绘图输入.json').read_text(encoding='utf-8'))
STYLE=json.loads((ROOT/'数据/绘图样式与标签.json').read_text(encoding='utf-8'))
C,L=STYLE['colors'],STYLE['labels'];OUT=ROOT/'绘图产物'

def setup():
    font_manager.findfont('Arial',fallback_to_default=False)
    mpl.rcParams.update({'font.family':'Arial','font.sans-serif':['Arial'],'font.size':9.5,
        'axes.labelsize':10,'xtick.labelsize':9.3,'ytick.labelsize':9.5,
        'svg.fonttype':'none','pdf.fonttype':42,'ps.fonttype':42,
        'text.color':C['text'],'axes.labelcolor':C['secondary'],
        'xtick.color':C['secondary'],'ytick.color':C['text'],
        'figure.facecolor':'white','axes.facecolor':'white','savefig.facecolor':'white'})

def axes(fig,box,rect):
    x,y,w,h=box;xx,yy,ww,hh=rect
    return fig.add_axes([x+w*xx,y+h*yy,w*ww,h*hh])

def label(ax,x,y,value,gid,**kwargs):
    return ax.text(x,y,value,transform=ax.transAxes,va='center',fontsize=9.5,gid=gid,**kwargs)

def draw_a(fig,box=(0,0,1,1)):
    arms=SNAP['arms']
    assert [(r['events'],r['verdicts']) for r in arms]==[(3,96),(23,96),(32,96),(18,96),(22,96)]
    ax=axes(fig,box,[.23,.285,.59,.55])
    ax.set(xlim=(0,40),ylim=(4.6,-.6))
    ax.set_xticks([0,10,20,30,40]);ax.set_yticks(range(5))
    ax.set_yticklabels([f"{a['arm'].replace(chr(39),'′')} · {a['label']}" for a in arms])
    ax.tick_params(axis='both',length=0,pad=7)
    ax.set_xlabel(L['a_axis'],labelpad=11)
    for spine in ax.spines.values():spine.set_visible(False)
    for i,a in enumerate(arms):
        # Rounded denominator rail is a real 0–40% reference, not a success score.
        rail=FancyBboxPatch((0,i-.25),40,.50,boxstyle='round,pad=0,rounding_size=0.23',
                           facecolor=C['track'],edgecolor='none',gid=f'denominator-rail-{i}')
        ax.add_patch(rail)
        bar=ax.barh(i,a['percent'],height=.50,color=C['control'] if i==0 else C['web'],edgecolor='none',zorder=2)[0]
        bar.set_gid(f'rate-bar-{i}');bar.set_clip_path(rail)
        ax.text(1.105,i,f"{a['percent']:.1f}%",transform=ax.get_yaxis_transform(),
                ha='right',va='center',fontsize=9.5,gid=f'percent-{i}',clip_on=False)
        ax.text(1.24,i,f"{a['events']}/{a['verdicts']}",transform=ax.get_yaxis_transform(),
                ha='right',va='center',fontsize=9.5,gid=f'events-verdicts-{i}',clip_on=False)
    ax.text(1.24,1.105,L['a_counts'],transform=ax.transAxes,ha='right',va='center',fontsize=9.3,gid='count-column-label')
    for i,t in enumerate(ax.get_yticklabels()):t.set_gid(f'condition-label-{i}')
    return ax

def band(ax,y,fraction,color,gid):
    ax.add_line(Line2D([0,1],[y,y],transform=ax.transAxes,color=C['border'],lw=.6,gid=gid+'-denominator'))
    if fraction:
        ax.add_patch(Rectangle((0,y),fraction,.105,transform=ax.transAxes,facecolor=color,edgecolor='none',gid=gid+'-numerator'))

def draw_b(fig,box=(0,0,1,1)):
    b,m=SNAP['boundary'],SNAP['integrity']
    width=.93/2.14;gap=width*.14
    left,right=[axes(fig,box,[x,.07,width,.87]) for x in [.04,.04+width+gap]]
    for ax in [left,right]:ax.set_axis_off();ax.set(xlim=(0,1),ylim=(0,1))
    label(left,0,.97,L['web_unit'],'occurrence-unit')
    label(right,0,.97,L['marker_unit'],'marker-unit')
    total=b['local_occurrences']+b['web_occurrences']
    label(left,0,.83,f"Local section: {b['local_occurrences']}",'local-occurrence-label')
    label(left,1,.83,f"Web section: {b['web_occurrences']}/{total}",'web-occurrence-label',ha='right')
    band(left,.65,b['web_occurrences']/total,C['web'],'web-allocation')
    for y,name,value,gid in [(.46,L['violations'],f"{b['local_violation_answers']}/{b['all_answers']} answers",'local-wn'),
                            (.26,L['local_url'],f"{b['local_url_answers']}/{b['all_answers']} answers",'local-url'),
                            (.06,L['pool_c'],f"{b['pool_c_with_web']}/{b['pool_c_answers']} answers",'pool-c')]:
        label(left,0,y,name,gid+'-label');label(left,1,y,value,gid+'-value',ha='right')
    for key,name,y,ybar,color in [('resolvable',L['resolvable'],.83,.65,C['resolvable']),
                                 ('out_of_pool',L['out_of_pool'],.46,.20,C['control'])]:
        label(right,0,y,name,'marker-label-'+key)
        label(right,1,y,f"{m[key]:,}/{m['markers']:,}",'marker-value-'+key,ha='right')
        band(right,ybar,m[key]/m['markers'],color,'marker-'+key)
    return left,right

def export(fig,name,**alignment):
    OUT.mkdir(exist_ok=True);prefix=OUT/name;fig.canvas.draw()
    if 'panel_ids' in alignment:
        alignment['row_groups']=[{'id':'structural-evidence-row','panels':alignment['panel_ids']}]
    require_matplotlib_panel_alignment(fig,json_out=str(prefix)+'.alignment.json',tolerance_pt=1.5,
        gutter_tolerance_pt=1.5,strict=True,require_panel_labels=False,**alignment)
    fig.savefig(str(prefix)+'.svg',bbox_inches=None,pad_inches=0)
    fig.savefig(str(prefix)+'.pdf',bbox_inches=None,pad_inches=0)
    fig.savefig(str(prefix)+'.eps',bbox_inches=None,pad_inches=0)
    fig.savefig(str(prefix)+'.png',dpi=600,bbox_inches=None,pad_inches=0)
    fig.savefig(str(prefix)+'.tiff',dpi=600,bbox_inches=None,pad_inches=0,pil_kwargs={'compression':'tiff_lzw'})
    with Image.open(str(prefix)+'.tiff') as im:
        im.convert('RGB').save(str(prefix)+'.tiff',dpi=(600,600),compression='tiff_lzw')
    plt.close(fig)

if __name__=='__main__':
    setup()
    fa=plt.figure(figsize=(174/25.4,58/25.4));draw_a(fa);export(fa,'图4a_旧架构联网混入风险')
    fb=plt.figure(figsize=(174/25.4,50/25.4));draw_b(fb);export(fb,'图4b_新架构结构核验',panel_ids=['occurrences-and-answers','citation-markers'])
    # Review master reuses the identical draw functions; its only added text is a,b.
    height=116
    review=plt.figure(figsize=(174/25.4,height/25.4))
    aa=draw_a(review,(0,58/height,1,58/height))
    bb=draw_b(review,(0,2/height,1,50/height))
    review.text(.025,.975,'a',fontsize=11,fontweight='bold',ha='left',va='top',gid='review-panel-a')
    review.text(.025,54/height,'b',fontsize=11,fontweight='bold',ha='left',va='top',gid='review-panel-b')
    export(review,'图4_ab_仅字母组图审阅版',axes=bb,panel_ids=['occurrences-and-answers','citation-markers'])
    print('Exported two independent live-text panels and a letter-only vector review master.')
