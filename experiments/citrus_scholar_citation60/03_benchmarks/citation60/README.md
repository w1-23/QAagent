# citation60 controlled-data boundary

Version: `4.4.0`
Status: `PROTECTED_L1_FAIL_CLOSED / G12_PENDING`

The citation60 benchmark is a planned supplementary experiment with 60 frozen
questions, one target chunk per question, and three conditions. Its question
texts, target chunk identifiers, source locators, runtime plan, and validity
rows are not released into the audit tree while G12 source and redistribution
review is pending.

The exact former public paths, byte counts, SHA-256 values, content kinds, and
release reasons are recorded in:

```text
12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json
```

On the isolated server, the controlled files are resolved below the directory
named by `CITRUS_EXPERIMENT_L1_ROOT`. A missing public question file is an
intentional protected-asset state, not a package error. A file marked
`PENDING_RELEASE_REVIEW` in the asset register must never appear in the public
tree.

The public documentation may state only:

- five corpus-source categories contributed 12 frozen chunks each;
- the sample is not representative of all citrus research;
- the only formal endpoint is deterministic target-chunk citation localization;
- `failed`, `cancelled`, and `skipped` remain in the intended denominator;
- no semantic-support rate is produced or reported.

Git history still contains prior public objects. Only the repository
owner/PI may decide whether to restrict repository access or request history
cleanup. This package does not authorize history rewriting or force pushing.
