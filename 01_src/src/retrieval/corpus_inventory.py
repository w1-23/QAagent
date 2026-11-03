"""Read-only metadata inventory for planning corpus index migrations.

This command prints batch/table counts and schema only; it never prints chunk text.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from src.retrieval.corpus_registry import active_lancedb_path, load_corpus_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--lance-dir")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).resolve()
    registry = load_corpus_registry(data_dir)
    lance_dir = Path(args.lance_dir).resolve() if args.lance_dir else active_lancedb_path(data_dir)
    sources: list[tuple[str, str, str, Path]] = []
    if registry.corpora:
        for corpus_id, spec in sorted(registry.corpora.items()):
            rel = str(spec.get("chunks_path") or "").strip()
            if not rel:
                rel = f"{spec['batch']}/chunks/chunks.jsonl"
            path = (data_dir / rel).resolve()
            if data_dir not in path.parents:
                raise ValueError(f"unsafe chunks path registered for {corpus_id}")
            sources.append((corpus_id, str(spec.get("family_id") or ""),
                            str(spec.get("source_type") or ""), path))
    else:
        paths = set(data_dir.glob("*/chunks/chunks.jsonl")) | set(data_dir.glob("*/chunks.jsonl"))
        for path in sorted(paths):
            batch = path.parent.parent.name if path.parent.name == "chunks" else path.parent.name
            sources.append((batch, "", "", path))

    expected_table_rows: dict[str, int] = {}
    for corpus_id, family_id, source_type, path in sources:
        if not path.is_file():
            print(f"CORPUS {corpus_id}: MISSING chunks file at {path}")
            continue
        count = 0
        categories: collections.Counter[str] = collections.Counter()
        families: collections.Counter[str] = collections.Counter()
        source_types: collections.Counter[str] = collections.Counter()
        keys: collections.Counter[str] = collections.Counter()
        schema_versions: collections.Counter[str] = collections.Counter()
        seen_ids: set[str] = set()
        duplicate_ids = 0
        missing_contract_rows = 0
        for line in path.open(encoding="utf-8"):
            row = json.loads(line)
            count += 1
            keys.update(row.keys())
            categories[str(row.get("l1_category") or row.get("category") or "<empty>")] += 1
            families[str(row.get("corpus_family_id") or row.get("family_id") or "<legacy>")] += 1
            source_types[str(row.get("source_type") or "<empty>")] += 1
            schema_versions[str(row.get("schema_version") or "<legacy>")] += 1
            chunk_id = str(row.get("chunk_id") or "")
            if chunk_id and chunk_id in seen_ids:
                duplicate_ids += 1
            if chunk_id:
                seen_ids.add(chunk_id)
            if not all(str(row.get(key) or "").strip() for key in
                       ("corpus_id", "corpus_family_id", "document_id", "chunk_id", "source", "text")):
                missing_contract_rows += 1
        print(f"CORPUS {corpus_id}: family={family_id or dict(families.most_common(1))}; "
              f"registered_source_type={source_type or '<legacy>'}; "
              f"rows={count}; families={dict(families.most_common())}; "
              f"categories={dict(categories.most_common())}; "
              f"source_types={dict(source_types.most_common())}; "
              f"schema_versions={dict(schema_versions.most_common())}; "
              f"duplicate_chunk_ids={duplicate_ids}; rows_missing_contract={missing_contract_rows}; "
              f"keys={sorted(keys)}")
        if registry.corpora and corpus_id in registry.corpora:
            expected_table_rows[str(registry.corpora[corpus_id].get("table_name") or corpus_id)] = count
        else:
            expected_table_rows[corpus_id] = count
    try:
        import lancedb
        db = lancedb.connect(str(lance_dir))
        names = sorted(db.table_names())
        print(f"LANCEDB path={lance_dir}; tables={len(names)}")
        missing = sorted(set(expected_table_rows) - set(names))
        extra = sorted(set(names) - set(expected_table_rows))
        if missing or extra:
            print(f"TABLE_SET_MISMATCH missing={missing}; extra={extra}")
        for name in names:
            table = db.open_table(name)
            rows = int(table.count_rows())
            status = "OK" if expected_table_rows.get(name) == rows else "ROW_COUNT_MISMATCH"
            print(f"TABLE {name}: rows={rows}; expected={expected_table_rows.get(name, '<unknown>')}; "
                  f"status={status}; schema={list(table.schema.names)}")
    except Exception as exc:
        print(f"LANCEDB unavailable: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
