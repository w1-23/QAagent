# S00 simulated dry run

This directory creates a deterministic, synthetic data set for interface and
retention testing. It does not call a model, CitrusScholar, a web service, or a
judge API.

Every generated record includes:

```text
evidence_class = SIMULATED_DRY_RUN
not_for_publication_claims = true
```

The generator consumes the real frozen question files and run plans, then
creates synthetic answers, citations, tool traces, workflow observations and
judge responses. This verifies field coverage, hash manifests and downstream
analysis plumbing. The generated values are not measurements and must never be
reported as experimental results.

Run:

```powershell
python scripts/S00_simulated_dry_run/00_generate_simulated_data.py
python scripts/S00_simulated_dry_run/10_verify_simulated_data.py
```

Generated locations:

```text
06_runs/simulated_dry_run/
09_analysis/simulated_dry_run/
```
