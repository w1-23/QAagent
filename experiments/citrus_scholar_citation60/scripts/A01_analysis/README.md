# A01 analysis

Strict analysis accepts only complete, hash-locked inputs and real outputs:

```powershell
python scripts/check_package.py --require-private-corpus
python scripts/analyze_and_plot.py --strict
```

The analysis must fail if any raw run, aggregated judge row, observation row,
workflow criterion or required artifact/SHA is missing. It never fills an
error with zero and never creates a figure from a placeholder.
