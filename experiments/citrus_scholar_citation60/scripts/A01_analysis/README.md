# A01 strict analysis

Protocol version: `4.4.0`
Status: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4`
Execution authorization: `NONE`

`run_strict_analysis.py` is the only current formal core-QA analysis entry.
It reads the protected 180-row human-consensus table and writes only below
`CITRUS_EXPERIMENT_L1_ROOT`. It refuses public-tree inputs or outputs and
refuses to overwrite an existing output directory.

The confirmatory endpoint is `egts_system_outcome` for the unique B2 versus B1
comparison over 60 planned question slots per condition. Authentic
infrastructure terminal states remain system-task failures. The separate
semantic endpoint `human_consensus_egts` is reported only for authentically
scorable answers, together with semantic coverage and infrastructure-failure
counts.

The implementation requires:

- 180 unique question-condition rows;
- 60 questions with exactly B0/B1/B2;
- all six frozen strata L/V/W/X/P/N;
- deterministic `egts_system_outcome` values of 0 or 1;
- no blocked consensus rows;
- exact two-sided McNemar and 10,000 paired question-within-stratum bootstrap
  replicates using `numpy.random.Generator(numpy.random.PCG64(20261104))`.

`analyze_and_plot.py` and the old per-experiment summary shim are retained only
as `LEGACY_NON_EXECUTABLE` provenance. The commands in their old documentation
must not be used.

No experiment or analysis is authorized by this candidate implementation.
The protected output root is an analysis/release boundary, not a restriction
on the Agent's normal access to the registered corpus.
