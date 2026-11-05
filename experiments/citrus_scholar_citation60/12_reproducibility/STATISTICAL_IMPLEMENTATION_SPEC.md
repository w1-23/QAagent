# Statistical implementation specification

Version: `4.3.1`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Analysis authority: `09_analysis/analysis_contract.md`

This file removes implementation ambiguity. It does not contain results.

## 1. General settings

```text
alpha = 0.05, two-sided
bootstrap_replicates = 10,000
bootstrap_seed = 20261104
bootstrap_rng = numpy.random.Generator(numpy.random.PCG64(20261104))
bootstrap_interval = percentile
bootstrap_quantiles = [0.025, 0.975]
wilson_z = 1.959963984540054
```

Python, NumPy, SciPy, pandas, and plotting library versions are recorded in
`12_reproducibility/dependency_lock.txt` before the formal analysis. The
analysis manifest records their installed versions and the exact command.
Unlocked versions cannot produce a confirmatory result.

## 2. Primary paired analysis

The unique confirmatory comparison is human-consensus EGTS, `B2 vs B1`.

For the 2x2 table, label a pair:

```text
b = B2 success, B1 failure
c = B2 failure, B1 success
n = number of paired intended runs
```

The paired difference is `(b-c)/n`, reported in percentage points. The exact
two-sided McNemar p-value is the two-sided binomial test of `b` successes in
`b+c` trials with null probability `0.5`. No continuity correction is used.

When `b+c=0`, report `p=1.0` by the stated exact rule and the paired difference
with its bootstrap interval.

## 3. Stratified paired bootstrap

Questions are the resampling unit. For each stratum `L/V/W/X/P/N`, resample its
question IDs with replacement at the frozen within-stratum question count.
Retain all B0/B1/B2 rows attached to each selected question. Recompute the
paired difference in every replicate.

The reported interval uses the `0.025` and `0.975` empirical percentiles
without BCa correction. Missing or invalid confirmatory inputs block the
analysis before bootstrap; they are not silently resampled away.

## 4. Wilson intervals

For `k` successes in `n` observations with `p_hat=k/n`, use:

```text
center = (p_hat + z^2/(2n)) / (1 + z^2/n)
half   = z*sqrt(p_hat*(1-p_hat)/n + z^2/(4n^2)) / (1 + z^2/n)
lower  = center - half
upper  = center + half
```

Clamp the interval to `[0,1]`. No continuity correction is used.

## 5. Gwet AC1

For a binary nominal field with two raters, let `p_a` be observed agreement and
`pi_1`, `pi_2` the pooled category prevalence estimates across coded pairs.

```text
p_e = (pi_1*(1-pi_1) + pi_2*(1-pi_2)) / (2-1)
AC1 = (p_a - p_e) / (1 - p_e)
```

If `p_e=1`, report AC1 as undefined and block its threshold claim.

## 6. Cohen kappa

Use the unweighted two-rater Cohen kappa:

```text
kappa = (p_o - p_e) / (1 - p_e)
```

where `p_o` is observed agreement and `p_e` is the sum of the two raters'
marginal category products. If `p_e=1`, report kappa as undefined.

## 7. Applicability and NA

- `answerability_correct`: two-expert pairs over all 60 questions.
- `fatal_error`: two-expert pairs over all 60 questions.
- `answer_correct`: only the 52 `ANSWERABLE` questions.
- `evidence_traceable`: only the 52 `ANSWERABLE` questions.
- `UNANSWERABLE` `not_applicable` values are excluded from
  `answer_correct` and `evidence_traceable` binary agreement.
- EGTS agreement uses deterministic EGTS over all intended runs with a valid
  human-consensus or documented infrastructure outcome.

Each agreement table reports its own applicable `n`. It is prohibited to
impute NA, count NA as disagreement, or count NA as agreement.

## 8. Confidence intervals for agreement

Use the same question-clustered bootstrap:

1. resample question IDs within the declared stratum;
2. retain all three condition rows for each selected question;
3. recompute raw agreement, AC1, and kappa on applicable pairs;
4. drop degenerate replicates from the percentile summary and report how many
   were dropped;
5. if more than 1% of replicates are degenerate, mark the interval unstable
   and report it as exploratory.

## 9. Output lineage

The analysis manifest records:

- input SHA and row count;
- protocol and script SHA;
- seed, RNG name, resampling code, replicate count, and interval method;
- package versions;
- applicability counts;
- transition-table cell counts;
- missing/degenerate replicate counts;
- strict flags and command;
- output table and figure SHA.

Every reported number must be reproducible from the retained row-level data.
