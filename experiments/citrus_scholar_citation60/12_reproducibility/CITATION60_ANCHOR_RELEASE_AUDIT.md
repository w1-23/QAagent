# Citation60 public-anchor release audit

Version: `4.3.1`
Status: `PASS_COMPRESSED_AUDIT_FACTS_ONLY / AWAITING_G12_LICENSE_REVIEW`
Audited UTC: `2026-11-05`

## Scope

The third external review flagged `questions_DRAFT.csv` for a file-level
release audit because it contains `anchor_for_audit`. The same audit also
covered `questions_FROZEN.jsonl`, which carries the frozen copy of that field.

The controlling complete chunk file remained in protected L1 storage and was
not copied into this public report. The comparison used the retained copy with
SHA-256
`87a207010e63bd607944e38f71df08d0c09d7c9ca86b62f2e62949c89eaef2f3`.

## Method

`scripts/E01_chunk_citation/60_audit_release_anchors.py` compares each audit
anchor with its matching protected target chunk. It reports only citation IDs
and contiguous-match lengths. It does not print source passages.

The audit checks both raw case-folded text and punctuation-normalized text.
It fails if either comparison reaches 50 contiguous characters, if any anchor
or chunk is missing, or if the two public files do not each contain exactly 60
unique citation IDs.

## Result

| Public file | Anchors | Max raw contiguous | Max normalized contiguous | At/over 50 |
|---|---:|---:|---:|---:|
| `questions_DRAFT.csv` | 60 | 46 | 49 | 0 |
| `questions_FROZEN.jsonl` | 60 | 46 | 49 | 0 |

Manual review of the longest matches found names, dates, numeric values,
taxonomic terms, and short factual phrases separated in the public anchor by
semicolons. No complete source sentence, paragraph, section, or complete chunk
was reproduced.

## Release decision

The anchors are retained as compressed audit facts, not as source-text
excerpts. Both public question files remain `L2` candidates, but their release
status is `PENDING_RELEASE_REVIEW`; this audit does not replace G12 source and
license verification and does not authorize redistribution of underlying
material.

If a later protocol revision adds a longer quotation, the new file must be
re-audited. A contiguous match of 50 or more characters fails this automation
and requires file-level review before release.
