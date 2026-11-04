# 12_reproducibility

Only redacted reproducibility artifacts; never copy private data, credentials or unsanitized transcripts here.

`ASSET_REGISTER_CURRENT.csv` is refreshed with:

```powershell
python scripts/refresh_asset_register.py
```

The refresh preserves existing asset IDs, updates bytes/SHA, adds newly present
files, and excludes the register's own SHA to avoid self-reference. The register
is a current snapshot, not the final release manifest.
