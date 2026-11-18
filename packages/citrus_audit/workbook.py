"""Annotated source copies, without implicit appendices or rescanning."""
from __future__ import annotations
import hashlib
import io
import json
import re
from pathlib import Path
from openpyxl import load_workbook, Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter
def _unique_sheet_title(wb, desired: str) -> str:
    """Choose a workbook metadata title without replacing an uploaded sheet."""
    base = re.sub(r"[\\/*?:\[\]]", "_", str(desired or "审查信息")).strip() or "审查信息"
    base = base[:31]
    existing = {str(name).casefold() for name in wb.sheetnames}
    if base.casefold() not in existing:
        return base
    for index in range(2, 1000):
        suffix = f"_{index}"
        candidate = base[:31 - len(suffix)] + suffix
        if candidate.casefold() not in existing:
            return candidate
    raise ValueError("工作簿中可用的审查工作表名称已耗尽")

_KINDS: dict[str, tuple[str, str, str]] = {
    "missing_cell": ("缺失", "需核查", "核对原始记录或实验台账，确认该值确实未测"),
    "missing_values": ("缺失", "需核查", "确认该列是否允许缺失，补齐或说明"),
    "non_numeric_cell": ("类型错误", "需核查", "确认是单位/符号/文本类别，还是录入错误"),
    "mixed_numeric_text": ("类型错误", "需核查", "分离单位或文本标记，保持数值列纯净"),
    "duplicate_row": ("重复", "需核查", "确认是重复录入还是合法的重复测量"),
    "duplicate_key": ("重复", "需核查", "确认样本/主键是否唯一，或改用分组编号"),
    "duplicate_composite_key": ("复合键重复", "需核查", "核对实验单位与访视/重复编号，确认该组合是否允许重复"),
    "required_field_absent": ("必填字段缺失", "需核查", "核对表头与显式必填字段映射"),
    "raw_numeric_tail": ("原始尾数集中", "需核查", "核对仪器精度、舍入与固定分母；描述频率不能解释为显著性"),
    "adjacent_equal_differences": ("相邻等差", "需核查", "核对原始顺序及计划剂量/时间网格"),
    "sliding_equal_differences": ("滑窗等差", "需核查", "核对原始顺序、窗口与容差；公式序列可合法出现"),
    "repeated_sequences": ("重复片段", "需核查", "对照两个原始片段，确认周期信号或合法重复"),
    "repeated_blocks": ("重复数值块", "需核查", "对照两个原始数值块，核对共享对照与导出过程"),
    "expected_experiment_structure": ("实验结构不符", "需核查", "核对显式设计、实验单位、重复数及排除政策"),
    "out_of_range": ("越界", "需核查", "与实验设计的合理范围比对后修正"),
    "logic_conflict": ("逻辑冲突", "需核查", "核对两列来源与计算关系"),
    "constant_field": ("常量列", "提示", "确认该字段是否有信息量"),
    "identical_columns": ("列重复", "需核查", "确认是否复制粘贴造成的重复列"),
    "constant_offset_columns": ("列关系", "需核查", "确认是否为换算列"),
    "constant_ratio_columns": ("列关系", "需核查", "确认是否为比例换算列"),
    "exact_linear_columns": ("列关系", "需核查", "确认是否为公式派生列"),
    "decimal_ending_concentration": ("末位集中", "提示", "确认是否为舍入或固定分母造成"),
    "unequal_group_size": ("分组不均", "提示", "确认是否按设计"),
    "unequal_replicates": ("重复数不一", "提示", "核对重复定义"),
    "integer_mean_inconsistent": ("均值不一致", "需核查", "核对数据粒度、舍入与 n 定义"),
    "integer_summary_inconsistent": ("统计量不一致", "需核查", "核对 SD 定义与舍入"),
    "invalid_group_mapping": ("字段未匹配", "提示", "修正分组字段名后重跑"),
    "invalid_value_mapping": ("字段未匹配", "提示", "修正测量字段名后重跑"),
    "design_checks_skipped": ("未做分组检查", "提示", "如需分组检查，请说明分组列"),
    "grim_sd_skipped": ("未做 SD 检查", "提示", "确认报告的是样本 SD 还是总体 SD"),
    "empty_table": ("空表", "阻断", "确认文件是否选错或表头行是否正确"),
    "empty_column": ("空列", "提示", "整列无数据，通常是公式列或预留空白列；如该列应有数据请核对导出方式"),
    "missing_row": ("缺行", "需核查", "确认该行是否被合并单元格或公式行占用"),
    "iqr_insufficient": ("样本不足", "提示", "组内样本不足 8，不做离群推断；如需判定请补充重复测量"),
    "iqr_outlier": ("离群值", "需核查", "对照原始记录确认该值是否为录入错误或真实生物学差异"),
    "mixed_numeric_text": ("类型混杂", "需核查", "统一该列的数值格式，或把文本说明移到备注列"),
    # 按行标注路径的结论，不是规则命中：措辞必须让读者看出这是模型意见。
    "row_annotation": ("按行标注", "需核查", "该条为人工/模型注释，请对照原始记录与实验设计确认"),
}

def _kind(check: str) -> tuple[str, str, str]:
    return _KINDS.get(check, (check or "其他", "提示", "人工复核"))


_RANK={'error':0,'warning':1,'review':1,'info':2,'ok':3}
_COVERAGE={'iqr_insufficient','design_checks_skipped','invalid_group_mapping','invalid_value_mapping',
           'grim_sd_skipped','integer_summary_not_checked','findings_truncated'}


def _sort(f):
    return (_RANK.get(f.get('severity',''),2),str(f.get('check','')),f.get('row') or 0,
            f.get('column_index') or 0,str(f.get('detail','')))


def _comment(findings, previous=''):
    ordered=sorted(findings,key=_sort)
    ref='findings:sha256:'+hashlib.sha256(json.dumps(ordered,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
    lines=[]
    if previous:lines.append('已有批注：'+' '.join(previous.split())[:250])
    for item in ordered[:10-len(lines)]:
        line='['+str(item.get('severity','review'))+'] '+str(item.get('check',''))+'：'+' '.join(str(item.get('detail','')).split())
        if len(lines)>=9 or sum(len(x)+1 for x in lines)+len(line)>1300:break
        lines.append(line)
    shown=len(lines)-(1 if previous else 0);overflow=len(ordered)-shown
    if overflow:lines.append(f'另有 {overflow} 条；完整发现 {ref}')
    elif len(lines)<10:lines.append('完整发现 '+ref)
    return '\n'.join(lines)[:1500],ref


def _data_signature(wb,bounds):
    state=[]
    for name,(height,width) in bounds.items():
        ws=wb[name]
        state.append([name,[str(x) for x in ws.merged_cells.ranges],
            [[c.coordinate,c.value,c.data_type,c.number_format] for row in ws.iter_rows(max_row=height,max_col=width) for c in row]])
    return hashlib.sha256(json.dumps(state,ensure_ascii=False,default=str,sort_keys=True).encode()).hexdigest()


def _restore_original_cells(original, exported):
    """Retain original cell XML (including empty inline strings/formula caches).

    openpyxl normalizes some empty cells on save. Overlay only exported style
    references, leaving new comments/relationships and optional explanations intact.
    """
    import copy
    import posixpath
    import zipfile
    import xml.etree.ElementTree as ET
    ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
        'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    def sheets(archive):
        relations={node.attrib['Id']:node.attrib['Target'] for node in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        result={}
        for node in ET.fromstring(archive.read('xl/workbook.xml')).find('m:sheets',ns):
            target=relations[node.attrib['{'+ns['r']+'}id']]
            result[node.attrib['name']]=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        return result
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(io.BytesIO(exported)) as output:
        source_sheets,output_sheets=sheets(source),sheets(output)
        strings=list(ET.fromstring(source.read('xl/sharedStrings.xml'))) if 'xl/sharedStrings.xml' in source.namelist() else []
        patches={}
        for name,old_path in source_sheets.items():
            new_path=output_sheets[name]
            old_root,new_root=ET.fromstring(source.read(old_path)),ET.fromstring(output.read(new_path))
            old_data,new_data=old_root.find('m:sheetData',ns),new_root.find('m:sheetData',ns)
            if old_data is None or new_data is None:
                continue
            rows={node.attrib['r']:node for node in new_data}
            for old_row in old_data:
                row=rows.get(old_row.attrib['r'])
                if row is None:
                    row=ET.SubElement(new_data,old_row.tag,dict(old_row.attrib))
                cells={node.attrib['r']:node for node in row}
                for old_cell in old_row:
                    existing=cells.get(old_cell.attrib['r'])
                    restored=copy.deepcopy(old_cell)
                    if restored.attrib.get('t')=='s':
                        # openpyxl writes inline strings and removes sharedStrings.xml.
                        # Preserve the text/rich runs rather than dangling source indexes.
                        value=restored.find('m:v',ns)
                        if value is None or value.text is None:
                            raise ValueError('Invalid source shared string')
                        shared=strings[int(value.text)]
                        restored.remove(value);restored.attrib['t']='inlineStr'
                        inline=ET.SubElement(restored,'{'+ns['m']+'}is')
                        for child in shared:inline.append(copy.deepcopy(child))
                    if existing is not None:
                        if 's' in existing.attrib:restored.attrib['s']=existing.attrib['s']
                        else:restored.attrib.pop('s',None)
                        index=list(row).index(existing);row.remove(existing);row.insert(index,restored)
                    else:
                        row.append(restored)
            patches[new_path]=ET.tostring(new_root,encoding='utf-8',xml_declaration=True)
        result=io.BytesIO()
        with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as archive:
            for entry in output.infolist():
                archive.writestr(entry,patches.get(entry.filename,output.read(entry.filename)))
        return result.getvalue()


def export_snapshot(snapshot, *, explain_visible=False, issue_list=False, overview=False, rules=False):
    """Render saved findings on reviewed bytes. All appendices opt in."""
    if any(type(v)is not bool for v in (explain_visible,issue_list,overview,rules)):
        raise ValueError('Export switches must be boolean')
    if hashlib.sha256(snapshot.source_bytes).hexdigest()!=snapshot.source_sha256:
        raise ValueError('Source snapshot changed')
    report=snapshot.report
    suffix=Path(snapshot.source_name).suffix.lower()
    if suffix in {'.xlsx','.xlsm'}:
        wb=load_workbook(io.BytesIO(snapshot.source_bytes),keep_vba=suffix=='.xlsm')
    else:
        from .envelope import TableEnvelope
        wb=Workbook(); wb.remove(wb.active)
        for entry in report['tables']:
            table=TableEnvelope.from_dict(entry['table'])
            name=_unique_sheet_title(wb,table.sheet or Path(table.source_name).stem or 'Sheet1')
            ws=wb.create_sheet(name);entry['output_sheet']=name
            for col,title in zip(table.column_numbers,table.columns):
                cell=ws.cell(table.header_row,col,title);cell.data_type='s'
            for row,values in zip(table.row_numbers,table.values):
                for col,value in zip(table.column_numbers,values):
                    if type(value)is int and len(str(abs(value)))>15:value=str(value)
                    cell=ws.cell(row,col);cell.value=value
                    if isinstance(value,str):cell.data_type='s'
        if not wb.sheetnames:raise ValueError('No table snapshot available for workbook export')
    original_names=list(wb.sheetnames)
    bounds={ws.title:(ws.max_row,ws.max_column) for ws in wb.worksheets}
    if sum(height*width for height,width in bounds.values()) > 2_000_000:
        raise ValueError('Workbook export exceeds 2000000-cell verification budget')
    before=_data_signature(wb,bounds)
    marks={}; all_findings=[]; full_refs={}
    for entry in report['tables']:
        table=entry['table']; name=entry.get('output_sheet',table['sheet'])
        if name not in bounds:raise ValueError('Snapshot worksheet missing')
        height,width=bounds[name]; header=table['header_row']
        for original in entry['result']['data']['findings']:
            item=dict(original); item['sheet']=name; all_findings.append(item)
            row=item.get('row'); col=item.get('column_index')
            if row not in (None,'') and (type(row)is not int or not header<row<=height):
                raise ValueError('Finding row outside reviewed source')
            if col not in (None,'') and (type(col)is not int or not 1<=col<=width):
                raise ValueError('Finding column outside reviewed source')
            coords=[];evidence=item.get('evidence') or {}
            for coord in evidence.get('cells',[]) if isinstance(evidence,dict) else []:
                r,c=coord.get('row'),coord.get('column_index')
                if type(r)is not int or type(c)is not int or not header<r<=height or not 1<=c<=width:
                    raise ValueError('Evidence cell outside reviewed source')
                coords.append((r,c,True))
            if item.get('coverage_only') or item.get('check') in _COVERAGE:continue
            if not coords:
                if row and col:coords=[(row,col,True)]
                elif row:coords=[(row,c,True) for c in range(1,width+1)]
                elif col:coords=[(header,col,False)]
                else:coords=[(1,1,False)]
            for r,c,color in coords:
                key=(name,r,c);bucket=marks.setdefault(key,{'findings':[],'color':False})
                bucket['findings'].append(item);bucket['color']|=color
    for (name,row,col),bucket in sorted(marks.items()):
        ws=wb[name];cell=ws.cell(row,col)
        if isinstance(cell,MergedCell):
            for region in ws.merged_cells.ranges:
                if cell.coordinate in region:
                    cell=ws.cell(region.min_row,region.min_col);break
        key=(name,cell.row,cell.column)
        full_refs.setdefault(key,{'findings':[],'color':False})
        full_refs[key]['findings'].extend(bucket['findings']);full_refs[key]['color']|=bucket['color']
    references={}
    for (name,row,col),bucket in sorted(full_refs.items()):
        cell=wb[name].cell(row,col)
        unique={json.dumps(x,ensure_ascii=False,sort_keys=True,default=str):x for x in bucket['findings']}
        text,ref=_comment(list(unique.values()),cell.comment.text if cell.comment else '')
        references[ref]=sorted(unique.values(),key=_sort)
        cell.comment=Comment(text,'Citrus Audit')
        if bucket['color']:
            cell.fill=PatternFill('solid',fgColor='FDE2E1')
            side=Side(style='thin',color='E06666');cell.border=Border(left=side,right=side,top=side,bottom=side)
    explanations=[]
    def put(ws,row,col,value):
        cell=ws.cell(row,col,value)
        if isinstance(value,str):cell.data_type='s'
    if explain_visible:
        for name,(height,width) in bounds.items():
            ws=wb[name];start=width+2
            put(ws,1,start,'审查解释（自动生成，非数据）')
            local=sorted((x for x in all_findings if x['sheet']==name and not x.get('coverage_only')),key=_sort)
            by_row={}
            for item in local:
                row=item.get('row')
                if type(row) is int:
                    by_row.setdefault(row,[]).append(str(item.get('check',''))+'：'+str(item.get('detail','')))
            for row,items in sorted(by_row.items()):
                put(ws,row,start,'\n'.join(items)[:32000])
            explanations.append({'sheet':name,'start_column':start,'rows':sorted(by_row),
                'column_table_findings':[x for x in local if not x.get('row')]})
    added=[]
    def appendix(name,headers,rows):
        ws=wb.create_sheet(_unique_sheet_title(wb,name));added.append(ws.title)
        for r,values in enumerate([headers,*rows],start=1):
            for c,value in enumerate(values,start=1):put(ws,r,c,value)
    if issue_list:
        appendix('问题清单',['工作表','行','列','规则','严重程度','说明'],
            [[x['sheet'],x.get('row') or '',x.get('column_index') or '',x.get('check',''),x.get('severity',''),str(x.get('detail',''))[:32000]]
             for x in sorted(all_findings,key=lambda x:(x['sheet'],*_sort(x)))])
    if overview:
        appendix('审查概览',['项目','值'],[['原始 SHA-256',snapshot.source_sha256],['状态',report['status']],
            ['已解析表',len(report['tables'])],['发现',len(all_findings)],['限制','发现仅为复核线索，不是科研不端判定']])
    if rules:
        appendix('审查规则',['工作表','规则','状态','方法版本','已检查','跳过','原因/范围'],
            [[entry['table']['sheet'],key,rule['status'],rule.get('method_version',''),rule.get('tested',0),rule.get('skipped',0),
              json.dumps(rule,ensure_ascii=False,default=str)[:32000]]
             for entry in report['tables'] for key,rule in entry['result']['data']['rule_execution']['rules'].items()])
    after=_data_signature(wb,bounds)
    if after!=before:raise ValueError('Export changed original values, types, formats or merges')
    output=io.BytesIO();wb.save(output);blob=output.getvalue()
    if suffix in {'.xlsx','.xlsm'}:
        blob=_restore_original_cells(snapshot.source_bytes,blob)
    reopened=load_workbook(io.BytesIO(blob),keep_vba=suffix=='.xlsm')
    if _data_signature(reopened,bounds) != before:
        raise ValueError('Serialized export changed original data')
    if not explain_visible and any((reopened[name].max_row,reopened[name].max_column)!=extent for name,extent in bounds.items()):
        raise ValueError('Serialized export expanded original data region')
    return blob,{'version':'2.0.0','source_sha256':snapshot.source_sha256,'export_sha256':hashlib.sha256(blob).hexdigest(),
        'original_sheets':original_names,'added_sheets':added,'explanation_blocks':explanations,
        'original_data_sha256':before,'exported_data_sha256':after,'data_region_unchanged':True,
        'marked_locations':len(references),'findings_references':references,'scan_reused':True,
        'source_representation':'original_workbook' if suffix in {'.xlsx','.xlsm'} else 'table_snapshot_conversion'}
