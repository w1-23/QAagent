# Decision addendum v4.3.3: fifth review and executable audit baseline

Decision version: `v4.3.3`
Status: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Baseline commit: `d45bf93897a4305b734a9a3d2abdfc0da03eb600`
Execution authorization: `NONE`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`

This addendum applies the fifth external review. It preserves every earlier
decision record as historical evidence and marks the over-broad v4.3.2
interpretation of public question disclosure as `SUPERSEDED`. It does not
authorize a server action, model call, Pilot, formal run, scoring operation, or
paper result.

## 1. Corrected threat model

The study evaluates the CitrusScholar platform end to end. The Agent must see
one current frozen question and may retrieve original literature through the
platform. A frozen question is a test input, not an answer leak.

The following objects are hidden from Agent inputs and Agent-accessible files:

- one-question `target_chunk_id` locators;
- direct injection of the target chunk text;
- Gold/reference answers and scoring mappings;
- expert or machine-judge decisions;
- another condition's answer, trace, or session state.

Public-safe frozen questions, scripts, source metadata, target chunk SHA-256,
and controlled reference copies may be used to audit the experiment. Full
source text remains subject to G12 redistribution review and therefore stays in
protected `L1`.

The previous v4.3.2 P0 wording that treated a public question set as proof of an
invalid experiment is `SUPERSEDED`. The replacement issue is narrower:
simulation provenance, release hygiene, and the Agent input/tool-access
boundary must be explicitly auditied.

## 2. Repository restriction evidence

The repository `w1-23/QAagent` was changed from Public to Private and verified
with the authenticated GitHub API on `2026-11-05T02:35:11Z`:

| Check | Result |
|---|---|
| visibility | `private=true` |
| authenticated permission | `admin=true` |
| default branch | `main` |
| `main` commit | `d45bf93897a4305b734a9a3d2abdfc0da03eb600` |
| `AUDIT_INDEX.md` readable | yes; blob `106a17c9186060132fdf82bfa48c80bf9f44afc1` |
| recursive tree readable | yes; 518 entries total, 316 experiment entries |
| commit history readable | yes |

The machine-readable record is
`01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05.json`. Historical Git
objects remain in history. They are no longer anonymously readable, but history
cleanup and repository deletion remain explicit owner decisions. No history was
rewritten and no force push occurred.

## 3. Locked experiment structure

The confirmatory system endpoint remains:

```text
egts_system_outcome
```

The separately reported semantic endpoint remains:

```text
human_consensus_egts
```

The fixed experiment sizes are:

| Experiment | Runs |
|---|---:|
| QA60 | 60 questions x B0/B1/B2 = 180 |
| Citation60 | 60 questions x B0/B1/B2 = 180 |
| Route192 | 192 |
| Multi24 | 24 |
| Citation repeat | 72 |
| Pilot | 12 questions x B0/B1/B2 = 36 |

Question IDs, run IDs, denominators, scoring tables, and analyses for QA60 and
Citation60 remain separate and are never pooled.

## 4. One question, one request, one raw result

Every frozen question-condition pair is a separate API request, Agent
execution, session, and raw result. B0, B1, and B2 receive independent requests
for the same question. Repeat72 uses a new session for every repeat.

The following are prohibited:

- placing multiple benchmark questions in one request;
- passing a previous answer into a later question or condition;
- sharing conversation memory across conditions or repeats;
- overwriting a raw result;
- deleting a failed run because it is inconvenient for the denominator.

The input-boundary audit requires `session_mode=NEW`, an empty
`conversation_history`, `question_count=1`, and exact equality with one frozen
question. Normal platform retrieval of the target paper is allowed and does not
count as leakage.

## 5. Reproducibility and execution order

The executable order is fixed:

1. Stage 0: repository, protected-asset, and data-lineage audit.
2. Stage 1: server, registration, login, OpenCode Go, Agent, admin, environment,
   bounded rate-limit, and model-identity probes, with every request attempt
   retained separately.
3. Stage 2: Agent input and tool-access boundary audit.
4. Stage 3: real B0/B1/B2 integration without guessed endpoints or fabricated
   tool results.
5. Stage 4: six condition-blinded expert batches, attestations, reviewer
   identity binding, append-only corrections, third-expert adjudication,
   raw-run SHA binding, deterministic consensus, and AC1/kappa/coverage.
6. Stage 5: 36-run Pilot. Formal runs can start only after Pilot acceptance and
   external review.
7. Stage 6: complete denominators, six strata, McNemar, bootstrap, Wilson
   intervals, and agreement.
8. Stage 7: Git bundle, at least two independent backups, frozen archive,
   dependency lock, SHA manifest, and licence/de-identification record.

All executable work occurs on the isolated server under
`/home/WZQ/citrus_experiment/`. The local workstation is limited to source and
protocol editing, static tests, and Git operations.

## 6. Expert and machine scoring

Two independent domain experts score every evaluable Pilot and formal QA60
answer. A third expert arbitrates any four-field disagreement. Their consensus
is the semantic measurement. The LLM judges are an independent automated
comparator and do not replace expert scoring.

Expert packages contain the question, one answer, allowed source scope,
citations, same-question frozen reference evidence, score sheet, codebook,
attestation, and completion checklist. They never contain condition, provider,
model, run ID, question ID, blind map, LLM score, or the other expert's score.

No Gold Answer is created. No semantic-support rate is calculated. Citation60
uses only deterministic target-chunk locator matching after the answer is
frozen.

Expert recruitment and external validity certification remain `PENDING` until
the data are frozen. This is a genuine human dependency, not something a script
may mark as passed.

## 7. Credentials and evidence

The API keys previously pasted into chat are treated as exposed and must be
rotated. No credential from the conversation is reused or copied into a file.

New credentials exist only in a Git-external server `EnvironmentFile` or
equivalent protected environment with directory mode `700` and file mode `600`.
The following variables are expected:

```text
CITRUS_AGENT_API_KEY
CITRUS_AGENT_BASE_URL
CITRUS_AGENT_MODEL_ID
CITRUS_JUDGE_API_KEY
CITRUS_JUDGE_BASE_URL
```

Request evidence stores a redacted body, endpoint path, `auth_present`, hashes,
actual model identity, status, usage/cost/allowance when returned, elapsed
time, and every attempt. It never stores an Authorization value, cookie, API
key, password, or secret-derived request-body hash.

## 8. What this work unit did not do

- no SSH, server, database, service, or environment execution;
- no Agent or Judge API request;
- no Pilot or formal run;
- no expert packet containing a real answer;
- no expert score, judgment, consensus, statistic, figure, or paper result;
- no gate status promotion;
- no history rewrite or force push.

The next stop is the sixth external GPT review. The review must inspect the
actual files, tests, repository visibility evidence, and candidate probe plan
before any server execution is authorized.
