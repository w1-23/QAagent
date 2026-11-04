# E02 route192

Input: `03_benchmarks/intent/intent192_CANDIDATE.csv` after lock.
Output: raw B2 traces and `07_scoring/reviews/intent_observations.csv`.

The endpoint is observable tool-action compliance, not hidden intent
classification. Missing tools or service-unavailable runs are reported
separately and excluded from the route-compliance denominator only through the
documented `available_1_0=0` rule.
