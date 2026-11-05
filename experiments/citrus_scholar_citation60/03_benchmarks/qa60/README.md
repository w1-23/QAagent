# Core QA60 validity

The former standalone Gold-generation protocol is superseded. The current
design requires two independent human certifications of question validity for
all 60 core questions and all 60 supplementary questions.

Authoritative protocol:

`../UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`

Build the shared 120-question packet:

```powershell
python scripts/build_validity_review_packet.py
```

The core question file remains active. Do not create Gold Answer, Gold Bundle or
Atomic-slot files. Core answer quality is evaluated by the v4.3.2
two-human-expert consensus with field-level third-expert adjudication;
`egts_system_outcome` retains the 60 planned system slots, while
`human_consensus_egts` is reported separately with semantic coverage.
GLM/MiMo/MiniMax provide a separate machine measurement. Existing judge runtime
and configuration candidates are not executable until a separately reviewed
implementation unit.
