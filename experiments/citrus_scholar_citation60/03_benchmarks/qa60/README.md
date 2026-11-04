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
Atomic-slot files. Core answer quality is evaluated by the frozen two-judge plus
adjudicator protocol in `05_baselines/judge_config.json`.
