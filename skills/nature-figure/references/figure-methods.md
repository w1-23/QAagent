# Figure methods: scientific form, composition and caption

On-demand guidance for the current General Agent figure path. Read this resource
with `read_skill(skill_id, version, sha256, resource="references/figure-methods.md")`
when a request actually needs a figure. It describes methods only: HostTools,
the capability executor, `visualization_enabled` and live sandbox readiness
still decide whether any figure tool may be called. A failed or partial read
affects this guidance alone and never narrows other authorized work.

## 1. Record the science before choosing a form

Write down, in the order it becomes known:

- the scientific question and the comparison or trend that answers it;
- the experimental unit (plant, fruit, plot, batch, participant) and the
  replicate count `n` behind every plotted mark;
- the raw values and their units, including the unit of any transformation;
- missing values and why they are missing; never fill, delete or truncate
  silently;
- the error definition (`sd`, `se`, `ci95`, exact interval) and the group that
  produced it;
- the test, its assumptions and its source, when inference is requested;
- each panel's evidence role: primary result, control, distribution check,
  effect size, or supporting mechanism;
- the final physical width and height in mm and the planned caption.

When the request needs no statistical test, say so and describe the observed
data faithfully. An attractive layout never justifies inventing a value, an
error bar, a p value or a replicate count.

## 2. Choose the rendering path

### Declarative six charts: `create_research_figure`

Use the declarative path when fields, units and order are explicit and one of
these forms answers the question:

| Chart | Use when | Required input |
|---|---|---|
| `bar` | Few discrete groups with a genuine aggregate and a stated error | `x`, `y`, optional `group`/`error` |
| `line` | Ordered domain (time, dose, rank) with a trend | `x`, `y`, optional `group`/`error` |
| `scatter` | Two measured variables; association without implying causation | `x`, `y`, optional `group` |
| `errorbar` | Point estimates with an explicitly defined uncertainty | `x`, `y`, `error` |
| `boxplot` | Distribution across a small number of real groups; show `n` | `y`, optional `group` |
| `histogram` | One variable's distribution and binning choice | `y`, optional `group` |

Supply data by `attachment` (this turn's table), `account_file` (library
`file_id` plus column) or `inline` (at most 200 rows). Do not default to
"first column against first numeric column"; identify the comparison columns
from the scientific question. Declarative specs support `panels` (1-12) with
`layout` rows/cols and `shared_legend`; use them only when the panels share one
scientific frame.

### Free Matplotlib/Seaborn: `run_research_figure_script`

Use a script when the six declarative forms are insufficient (for example a
heatmap, a forest plot, a radar chart or a custom scientific annotation). The
script receives `data` (named inputs) and `parameters` (explicit JSON) and must
run only in the authorized sandbox. The browser never executes model Python.

Prefer the v2 output contract:

1. The analysis script reads declared inputs, computes real summaries and calls
   `emit.table(name, frame, parents=[...])` for each value another panel needs.
2. A separate `render_script` receives those tables as `data[name]` and builds
   exactly one `Figure`. It must not reread files, recompute statistics or
   replace frozen values with new ones.
3. Declare every table the renderer uses as a `parents` entry, and let the host
   bind the per-table hashes. Frozen render receipts must match the renderer
   source and every table payload byte-for-byte.
4. Do not round-trip values through a lossy text format when an exact typed
   table is available.

## 3. Templates are composable options, not a routing table

Discover available templates from the actual preset catalog at run time. Do not
assume an ID exists because prose mentions it, and do not map chart names to
hard-coded code paths. When the catalog publishes them, the Nature templates are:

- `nature_volcano`: keep every tested point, plot effect against significance,
  and report threshold definitions and counts on both sides;
- `nature_roc`: sort by false-positive rate, retain vertical segments for tied
  scores, and compute the trapezoidal AUC from the plotted order;
- `nature_dotplot`: preserve a stable category order and state what dot size and
  color encode;
- `nature_marginal`: joint and marginal marks must use the same source rows,
  units and transformations;
- `nature_paired`: exactly two conditions per identifier; reject duplicate or
  incomplete pairs unless an explicit exclusion list is recorded.

A template supplies a declared table and a renderer for its own figure. To mix
it with basic plots, compose a new single v2 figure rather than stringing two
figures together.

## 4. Compose panels in one v2 figure

- One figure may contain up to 12 panels. Give each panel a stable `panel_id`
  or locator, and state its evidence role in the caption.
- Compose basic plots and template-derived tables in the same analysis run:
  emit each real table once, then render all panels from `data[...]`.
- Use nested `GridSpec` for asymmetric evidence architecture. A three-panel
  group can be a wide hero panel plus two equal companions, or one clearly
  supporting panel beside a primary panel. Asymmetry must encode scientific
  role, not decoration.
- Keep the widths of equal-span panels in a three-or-more-panel row equal; the
  render-time alignment gate checks this. Use spanning cells for the hero.
- Do not duplicate one observation as if it were independent evidence in two
  panels. Do not sample, pad or interpolate points to make a layout work.
- Label panels with a single bold letter near the panel's top-left; keep the
  label inside the canvas and away from data strokes so PDF collision checks
  stay meaningful.

## 5. Caption and QA honesty

A complete caption states what each panel shows, the experimental unit and `n`,
the error definition, the test and its source when applicable, and the main
comparison. It does not claim causality, mechanism or journal compliance beyond
the evidence.

Report the real export bytes and the real QA receipt. Missing visual inspection
remains `not_run`; an advisory source preflight remains advisory. Formats that
fail, unsupported editable geometry and unavailable checks are reported as such
and never replaced by a re-rendered file with a different hash. R execution,
external image generation and network calls are not available through this
method.

## 6. Admission

This document grants no tool, network, write or execution right. The only
admissible figure operations are those actually published for the current
request and still authorized when dispatched. `visualization_enabled=false`
hides figure tools even if this file was read; a missing or partial skill read
never blocks unrelated capabilities.
