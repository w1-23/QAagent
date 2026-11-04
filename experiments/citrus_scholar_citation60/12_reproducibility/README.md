# 12_reproducibility

The complete reproducibility chain is versioned, including frozen source text,
raw runs, judge responses, scores, figures and manuscript sources. Credentials,
Cookie values and personal authentication records are never copied here.

`ASSET_REGISTER_CURRENT.csv` is refreshed with:

```powershell
python scripts/refresh_asset_register.py
```

The refresh preserves existing asset IDs, updates bytes/SHA, adds newly present
files, and excludes the register's own SHA to avoid self-reference. The register
is a current snapshot, not the final release manifest.
