# Experiment package, repository, and release scope

Version: `4.4.0`
Status: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4`
Decision record:
`00_protocol/DECISION_ADDENDUM_2026-11-05_V4_4_REAL_AGENT_EXECUTION.md`
Real execution status: `NOT_STARTED`

The GitHub repository `w1-23/QAagent` is public so external reviewers can
read the reviewed source, plans, gate states, and retained evidence. Public
repository access is an audit mechanism. It is not redistribution permission
for source text whose licence or access conditions do not allow republication.

The v4.4.0 design evaluates the real CitrusScholar system. Test questions are
normal Agent input, and the Agent may retrieve, read, quote, and cite the
registered corpus through CitrusScholar. Retrieved source IDs, chunk IDs,
chunk text, titles, and excerpts are valid execution evidence. Neither the L1
retention root nor a hidden-question/hidden-chunk boundary is a prerequisite
for real Agent execution.

## 1. Required data classes

| Class | Content | Repository and release rule |
|---|---|---|
| `L0` | API keys, passwords, cookies, tokens, authenticated session values, private identity linkage | **Prohibited everywhere in Git, evidence, screenshots, and logs** |
| `L1` | Full text or substantial excerpts without redistribution permission, restricted supplements, private SOPs, identity maps, and unredacted sensitive logs | Retained in protected server storage; never committed merely for convenience |
| `L2` | Questions, permitted excerpts, source locators, redacted run records, hashes, blinded scores, adjudications, statistics, figures, and manuscript materials | May enter the public audit repository only after file-specific release checks |
| `SIMULATED_DRY_RUN` | Deterministic interface fixtures | Clearly labelled; never treated as Agent evidence, a research result, or part of a formal denominator |

Normal Agent access to the corpus does not reclassify the corpus as public.
Experiment execution rights and redistribution rights are separate decisions.

## 2. Retained evidence

The server experiment archive retains:

- the exact request, request SHA-256, request and session IDs, and account
  pseudonym;
- SSE events, timing, the raw Agent answer, reviewed final answer, persisted
  answer, citations, and source locators;
- every retrieval query, source/chunk identifier, rank or score when exposed,
  tool invocation, tool status, result hash, and backend-log reference;
- actual provider/model/response identity, usage, cost, allowance, errors,
  retries, cancellations, and terminal status;
- generated artifacts and their SHA-256 values;
- redacted Judge requests, complete safe raw responses, every attempt, parsed
  scores, and deterministic aggregation provenance;
- two independent expert score submissions, field-level adjudications,
  corrections, and attestations where the scored phase has begun;
- environment, model, index, prompt, and configuration snapshots;
- analysis inputs, scripts, seeds, outputs, figures, and manuscript source.

Missing or failed evidence is retained and marked `BLOCKED`, `INCOMPLETE`, or
the applicable terminal status. It is never replaced with an invented value.

## 3. Release checks

Every file proposed for Git must pass:

1. secret, credential, password, cookie, and Authorization-value scanning;
2. personal and account-identifier review;
3. signed or tokenized URL review;
4. private attachment, private SOP, and source-redistribution review;
5. blind-ID and mapping separation;
6. schema, malformed-link, and checksum verification;
7. manifest classification and SHA-256 registration.

A failed check keeps the original on protected server storage and blocks its
Git release. Redaction must preserve lineage back to the retained original.

## 4. Git contents for this review unit

This revision contains plans, protocol documents, candidate configuration,
templates, offline scripts, tests, a legacy-history exposure audit, and blank
review materials. It contains:

- no credential or `.env` file;
- no real Agent, Judge, database, Pilot, or formal result;
- no copied full corpus;
- no expert score;
- no paper result or fabricated numeric claim.

The existing public Git history contains a removed
`03_benchmarks/citation60/chunks_FROZEN.jsonl` blob with actual source text.
`01_preflight/LEGACY_CITATION60_SOURCE_TEXT_EXPOSURE_AUDIT.json` records its
object hash, size, record count, source count, and redistribution risk without
repeating the text. The PI authorized public audit access and did not authorize
a history rewrite. This is a release-rights issue, not an Agent access
restriction and not a blocker for real Agent testing.

## 5. Release manifest

`12_reproducibility/release_manifest.json` is the fail-closed release registry.
It is currently `NOT_GENERATED` with no entries because this review unit has no
real run or result derivative. For each proposed release file it will record:

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

`release_status` is one of `PUBLIC_RELEASED`,
`PROTECTED_SERVER_ONLY`, or `REDACTED_PUBLIC_DERIVATIVE`.
`PENDING_RELEASE_REVIEW` is a blocking working state, never a release claim.

## 6. Verification boundary

This commit authorizes no real model call, account registration, server
experiment, Pilot, expert scoring cycle, formal run, analysis result, or
manuscript result. Stage 0-2 and every gate remain pending server evidence and
external GPT review of this exact commit/tree.
