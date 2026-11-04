# Experiment package and public-release scope

Version: `4.3`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Decision record: `00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`

The GitHub repository is public. All experimental evidence must be retained,
but not all raw evidence may be publicly redistributed. Data retention and
public release are separate requirements.

## 1. Required data classes

| Class | Content | Public GitHub repository |
|---|---|---|
| `L0` | API keys, cookies, passwords, tokens, authentication headers, private identity linkage | **Prohibited** |
| `L1` | Full text or substantial excerpts without redistribution permission, restricted supplements, private SOPs, identity maps, raw sensitive logs, attachments or logs containing personal/session data | **Prohibited**; protected server storage only |
| `L2` | Questions and permitted excerpts, source locators, redacted run records, hashes, blinded scores, adjudications, statistics, figures, manuscript materials | **Allowed only after release review** |

The fact that a paper is readable online does not imply that its full text or
large excerpts may be redistributed. Access status and redistribution rights
are recorded separately.

## 2. Retained but not necessarily public

The following are retained for audit:

- authentic request, SSE, tool trace, answer, citations, failures, retries,
  generated files, and SHA values;
- two original expert score submissions, field adjudications, issue and
  correction records, and attestations;
- complete J1/J2/J3 redacted requests and safe raw responses;
- environment, model, source, index, and configuration snapshots;
- analysis inputs, scripts, seeds, outputs, figures, and manuscript source.

When a record is `L1`, the public package contains:

- source ID and source SHA-256;
- locator such as page, section, paragraph, or chunk;
- access class and permission/license status;
- redaction log and public-derivative SHA-256;
- an explicit statement that the original is retained in protected storage.

Never delete the original merely to make a public release possible.

## 3. Release checks

Every planned public file must pass:

1. `L0` secret and credential scan;
2. identity and account-information review;
3. signed-URL, tokenized-URL, and Authorization-header review;
4. attachment and private-SOP leakage review;
5. source-redistribution-rights review;
6. blind-ID and mapping separation;
7. malformed-link and checksum verification;
8. manifest classification and SHA verification.

A failed release check leaves the file out of GitHub while preserving it in
server-controlled storage. The omission and redaction lineage remain
auditable.

## 4. Included in the versioned experiment package

- protocol, decisions, gate ledger, retention ledger, and manuscript plan;
- core QA60 and supplementary citation60 frozen inputs;
- permitted source metadata and locators;
- routing, composite, and workflow definitions;
- code and configuration records after implementation review;
- `L2` redacted runs, score records, machine responses, analyses, and figures;
- synthetic fixtures and simulated dry-run data, always visibly labelled;
- release manifest, checksums, and redaction records.

## 5. Excluded or protected

- every `L0` credential and authentication record;
- every `L1` source or log that cannot be cleared for public release;
- model weights and runtime caches;
- identity linkage that could re-identify an expert or participant;
- unredacted server paths or signed access URLs that grant privileged access.

## 6. Release manifest

`12_reproducibility/release_manifest.json` records for each planned release
file:

```text
path
data_class
sha256
source_ids
license_or_access_status
redaction_record_id
public_derivative_of
reviewer_pseudonym
reviewed_utc
release_status
```

`release_status` is one of `PUBLIC_RELEASED`, `PROTECTED_SERVER_ONLY`, or
`REDACTED_PUBLIC_DERIVATIVE`. A missing manifest row blocks release.
`reviewer_pseudonym` is a non-reversible public code; the identity map remains
`L1` in protected server storage.

## 7. Verification boundary

This repository review unit contains protocol and blank schemas only. It does
not assert that any future release file has passed these checks. Actual
package, model, database, Pilot, scoring, and analysis checks run only on the
isolated server after external review.
