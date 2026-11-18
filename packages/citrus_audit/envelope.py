"""Serializable source coordinates and lexical evidence; no implicit host state."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import re
from typing import Any
from .integrity import _number


def _scalar(value):
    if value is None or type(value).__name__ in {'NAType','NaTType'}:
        return None
    if hasattr(value,'item'):
        value=value.item()
    if isinstance(value,Decimal):
        return {'decimal':str(value)}
    if isinstance(value,float):
        import math
        if not math.isfinite(value):
            return {'float':repr(value)}
    if type(value) in {str,int,float,bool}:
        return value
    if hasattr(value,'isoformat'):
        return {'datetime':value.isoformat()}
    raise ValueError('Unsupported table cell type')


def _restore(value):
    if not isinstance(value,dict):
        return value
    if set(value)=={'decimal'}:return Decimal(value['decimal'])
    if set(value)=={'float'} and value['float'] in {'nan','inf','-inf'}:return float(value['float'])
    if set(value)=={'datetime'}:
        from datetime import datetime
        return datetime.fromisoformat(value['datetime'])
    raise ValueError('Unsupported serialized cell')


@dataclass(frozen=True)
class TableEnvelope:
    columns: tuple[str,...]
    values: tuple[tuple[Any,...],...]
    row_numbers: tuple[int,...]
    column_numbers: tuple[int,...]
    source_sha256: str
    source_name: str = ''
    sheet: str = ''
    header_row: int = 1
    parser_limits: dict = field(default_factory=dict)
    precision_evidence: tuple[dict,...] = ()
    lexical_coverage: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    version: int = 2

    def __post_init__(self):
        if self.version!=2 or not re.fullmatch('[a-f0-9]{64}',self.source_sha256):
            raise ValueError('Envelope requires v2 and original source SHA-256')
        if (len(self.columns)!=len(self.column_numbers) or len(set(self.columns))!=len(self.columns)
                or any(not isinstance(c,str) or not c for c in self.columns)
                or len(self.values)!=len(self.row_numbers)
                or any(len(row)!=len(self.columns) for row in self.values)):
            raise ValueError('Envelope dimensions or column identities invalid')
        for coords in (self.row_numbers,self.column_numbers):
            if len(set(coords))!=len(coords) or any(type(v)is not int or v<1 for v in coords):
                raise ValueError('Original coordinates must be unique positive integers')
        if type(self.header_row)is not int or self.header_row<1 or any(r<=self.header_row for r in self.row_numbers):
            raise ValueError('Original data rows must follow header')
        seen=set()
        row_positions={value:index for index,value in enumerate(self.row_numbers)}
        col_positions={value:index for index,value in enumerate(self.column_numbers)}
        for evidence in self.precision_evidence:
            if not isinstance(evidence,dict) or set(evidence)!={'row','column','raw','places','exp'}:
                raise ValueError('Invalid lexical evidence shape')
            row,col=evidence['row'],evidence['column']
            if row not in row_positions or col not in col_positions or (row,col) in seen:
                raise ValueError('Lexical evidence coordinate invalid')
            seen.add((row,col)); raw=evidence['raw']
            if not isinstance(raw,str) or len(raw)>256 or _number(raw)[0] is None:
                raise ValueError('Lexical evidence must be a bounded actual token')
            match=re.fullmatch(r'[+-]?(?:[0-9]+(?:\.([0-9]*))?|\.([0-9]+))(?:[eE]([+-]?[0-9]+))?',raw.strip())
            if not match or type(evidence['places'])is not int or type(evidence['exp'])is not int:
                raise ValueError('Invalid raw precision')
            if evidence['places']!=len(match[1] or match[2] or '') or evidence['exp']!=int(match[3] or 0):
                raise ValueError('Precision metadata differs from actual token')
            value=self.values[row_positions[row]][col_positions[col]]
            if not isinstance(value,str) or value.strip()!=raw.strip():
                raise ValueError('Raw evidence cannot manufacture digits for numeric cells')

    def to_dict(self):
        return {'version':self.version,'columns':list(self.columns),
            'values':[[_scalar(v) for v in row] for row in self.values],
            'row_numbers':list(self.row_numbers),'column_numbers':list(self.column_numbers),
            'source_sha256':self.source_sha256,'source_name':self.source_name,'sheet':self.sheet,
            'header_row':self.header_row,'parser_limits':self.parser_limits,
            'precision_evidence':list(self.precision_evidence),'lexical_coverage':self.lexical_coverage,
            'coverage':self.coverage}

    @classmethod
    def from_dict(cls,data):
        data=dict(data)
        data['values']=tuple(tuple(_restore(v) for v in row) for row in data['values'])
        for key in ('columns','row_numbers','column_numbers','precision_evidence'):
            data[key]=tuple(data[key])
        return cls(**data)

    def to_frame(self):
        import pandas as pd
        frame=pd.DataFrame(self.values,columns=self.columns,index=self.row_numbers,dtype=object)
        column_names=dict(zip(self.column_numbers,self.columns))
        frame.attrs['_validated_precision_tokens']={(e['row'],column_names[e['column']]):e['raw'] for e in self.precision_evidence}
        frame.attrs['_column_numbers']=dict(zip(self.columns,self.column_numbers))
        return frame
