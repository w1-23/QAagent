"""Native Microsoft Word round-trip, restricted to synthetic engineering output.

A WPS registration for Word.Application is not Microsoft Word acceptance.
Never attach to or close an existing application; preserve original input bytes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if source.name != 'SYNTHETIC_results.docx':
        raise ValueError('Only the named synthetic engineering result is accepted')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    import winreg
    clsid = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, r'Word.Application\CLSID')
    server = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, 'CLSID\\' + clsid + r'\LocalServer32')
    executable = re.match(r'^\s*"?(.+?\.exe)(?:"|\s|$)', server, re.IGNORECASE)
    native_word = bool(executable and Path(executable.group(1)).name.casefold() == 'winword.exe')
    evidence = {'status': 'BLOCKED', 'checks': [], 'input_sha256': original_sha,
                'native_word_available': native_word,
                'registered_executable_name': Path(executable.group(1)).name if executable else 'unrecognized',
                'limits': ['Synthetic engineering input only', 'No model, GPU or server acceptance']}
    checks = evidence['checks']
    def persist():
        (output / 'checks.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    if not native_word:
        evidence['reason'] = 'Word.Application is not registered to Microsoft WINWORD.EXE; no native Word launched'
        persist()
        print(json.dumps(evidence, ensure_ascii=False))
        return 2
    import psutil
    import pythoncom
    import win32com.client
    import win32process
    existing = {p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').casefold() == 'winword.exe'}
    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition), 'source': 'actual_microsoft_word'})
        if not condition:
            raise AssertionError(name)
    pythoncom.CoInitialize()
    app = document = None
    owned = False
    try:
        app = win32com.client.DispatchEx('Word.Application')
        # Word exposes HWND on a document Window, not on every Application version.
        document = app.Documents.Add(Visible=False)
        pid = win32process.GetWindowThreadProcessId(document.ActiveWindow.Hwnd)[1]
        owned = pid not in existing
        check('Automation uses a new owned Word process', owned)
        app.Visible = False
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3
        document.Close(SaveChanges=0)
        document = None
        document = app.Documents.Open(str(source), ConfirmConversions=False, ReadOnly=False,
                                      AddToRecentFiles=False, Visible=False, OpenAndRepair=False)
        check('Word opens generated DOCX', document is not None)
        check('Word sees actual analysis table', document.Tables.Count >= 1)
        check('Word preserves full precision result', '3.8333333333333335' in document.Content.Text)
        marker = 'SYNTHETIC Microsoft Word edit-and-save engineering acceptance.'
        tail = document.Range(document.Content.End - 1, document.Content.End - 1)
        tail.InsertAfter('\r' + marker + '\r')
        saved = output / 'SYNTHETIC_word_roundtrip.docx'
        document.SaveAs2(str(saved), FileFormat=16, AddToRecentFiles=False)
        document.Repaginate()
        evidence['pages'] = int(document.ComputeStatistics(2))
        check('Actual Word pagination produces pages', evidence['pages'] > 0)
        document.ExportAsFixedFormat(str(output / 'SYNTHETIC_word_roundtrip.pdf'), 17)
        document.Close(SaveChanges=0)
        document = None
        document = app.Documents.Open(str(saved), ConfirmConversions=False, ReadOnly=True,
                                      AddToRecentFiles=False, Visible=False, OpenAndRepair=False)
        check('Word reopens actual saved edit', marker in document.Content.Text)
        check('Reopened Word retains table and precision', document.Tables.Count >= 1 and
              '3.8333333333333335' in document.Content.Text)
        check('Original publisher DOCX unchanged', hashlib.sha256(source.read_bytes()).hexdigest() == original_sha)
        with zipfile.ZipFile(saved) as package:
            check('Saved package retains document XML', 'word/document.xml' in package.namelist())
        evidence.update(status='PASSED', word_version=str(app.Version), owned_pid=pid,
                        saved_sha256=hashlib.sha256(saved.read_bytes()).hexdigest())
    except Exception as exc:
        evidence.update(status='FAILED', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        try:
            if document is not None and owned:
                document.Close(SaveChanges=0)
        finally:
            try:
                if app is not None and owned:
                    app.Quit(SaveChanges=0)
            finally:
                pythoncom.CoUninitialize()
                persist()
    print(str(output / 'checks.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
