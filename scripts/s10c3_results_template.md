{{ HEADER }}

# S10c dev run results — provisional (GPT), dev only

## Plain summary

- What we tested: whether the LLM layer of `get_applicable_standards` (final = max(rule, LLM)) catches mandatory ISO 10993 parts that the rules miss. Dev set: {{ strata_count }} strata profiles ({{ trap_count }} trap profiles reported separately), {{ n_efforts }} efforts × {{ repeats }} repeats; classifier Azure gpt-5-mini; provisional (GPT), dev only.
- The tool still misses mandatory parts: under-call U/P = {{ primary_medium_under_call }} at medium (tipping range at E1: {{ tipping_medium_e1 }}; at E2: {{ tipping_medium_e2 }}) and {{ primary_high_under_call }} at high (tipping range at E1: {{ tipping_high_e1 }}; at E2: {{ tipping_high_e2 }}), against {{ primary_rules_under_call }} for the rules alone.
- The misses sit in judgement items: rules-only under-call is {{ tag_judgement_rules }} on judgement items and {{ tag_matrix_settled_rules }} on matrix_settled and {{ tag_always_rule_rules }} on always_rule items.
- The LLM barely helps and adds over-calls: it removed {{ med_removed_frac }} misses at medium and {{ high_removed_frac }} at high (a reduction of 0 or more is guaranteed by design). Paired difference high − medium = {{ paired_difference }} (approximate (few profiles)), {{ paired_wording }}; exact sign-flip p = {{ sign_flip_p }}. Over-call rises by {{ sec_med_over_call_lift }} at medium and {{ sec_high_over_call_lift }} at high (approximate (few profiles) intervals).
- Effort rule (S10P-14): tier {{ effort_tier }} → {{ effort_choice }}, the same under all {{ n_versions }} reference versions. The holdout has not been run; it stays sealed until Final, after a Claude rerun.

## Run health

- **LLM detail counts (all {{ dev_profile_count }} profiles, {{ medium_runs }} runs per arm: {{ strata_runs_per_arm }} strata + {{ trap_runs_per_arm }} trap)**:
  - Medium effort: ok = {{ llm_detail_medium_ok }} ({{ llm_detail_medium_strata_ok }} strata + {{ llm_detail_medium_trap_ok }} trap), non-ok = {{ llm_detail_medium_non_ok }}
  - High effort: ok = {{ llm_detail_high_ok }} ({{ llm_detail_high_strata_ok }} strata + {{ llm_detail_high_trap_ok }} trap), non-ok = {{ llm_detail_high_non_ok }}
- **Runs and retries**:
  - Total planned runs: {{ total_runs }} ({{ medium_runs }} medium, {{ high_runs }} high); completed runs: {{ total_runs }}.
  - Main run HTTP retries: {{ main_retries }}.
  - Pilot run HTTP retries: {{ pilot_retries }} (connection errors, diagnosed as rare short network blips, decision D36; gate and watchdog added).
- **Paid requests (global ledger)**:
  - Candidates: {{ candidates_paid_requests }} requests (global ledger {{ candidates_global_start }} -> {{ candidates_global_end }}).
  - Pilot: {{ pilot_paid_requests }} requests (global ledger {{ pilot_global_start }} -> {{ pilot_global_end }}).
  - Main: {{ main_paid_requests }} requests (global ledger {{ main_global_start }} -> {{ main_global_end }}).
  - Total paid requests: {{ total_paid_requests }} against the global cap of {{ paid_call_cap }}.
- **Azure Monitor reconciliation**:
  {{ AZURE_RECONCILIATION }}

## Primary metric

| Metric | Rules-only | Medium hybrid | High hybrid | Reduction (rules − hybrid) |
|---|---|---|---|---|
| Primary under-call rate | {{ primary_medium_rules_under_call }} | {{ primary_medium_under_call }} | {{ primary_high_under_call }} | Medium: {{ lift_medium_reduction }} {{ lift_medium_ci }}; High: {{ lift_high_reduction }} {{ lift_high_ci }} |
| Tipping range at E1 | - | {{ tipping_medium_e1 }} | {{ tipping_high_e1 }} | - |
| Tipping range at E2 | - | {{ tipping_medium_e2 }} | {{ tipping_high_e2 }} | - |
| Profile-level under-call (any of {{ repeats }} repeats, exact Clopper-Pearson) | {{ profiles_rules_under_call }} | {{ profiles_medium_under_call }} {{ profiles_medium_ci }} | {{ profiles_high_under_call }} {{ profiles_high_ci }} | - |

Bootstrap intervals are approximate (few profiles); Clopper-Pearson intervals are exact.

- Medium under-call U/P = {{ tipping_medium_u_p }} (tipping range at E1: {{ tipping_medium_e1 }}; at E2: {{ tipping_medium_e2 }}).
- High under-call U/P = {{ tipping_high_u_p }} (tipping range at E1: {{ tipping_high_e1 }}; at E2: {{ tipping_high_e2 }}).

{{ STRUCTURAL_NOTE }}

Rules-only under-call rate check: medium = {{ primary_medium_rules_under_call }}, high = {{ primary_high_rules_under_call }} (equal: {{ rules_rates_equal_str }}).

### Split by tag

| Tag | Rules-only rate | Medium under-call rate | High under-call rate | Medium reduction (rules − hybrid) | High reduction (rules − hybrid) |
|---|---|---|---|---|---|
| `always_rule` | {{ tag_always_rule_rules }} | {{ tag_always_rule_med }} | {{ tag_always_rule_high }} | {{ tag_always_rule_med_lift }} | {{ tag_always_rule_high_lift }} |
| `matrix_settled` | {{ tag_matrix_settled_rules }} | {{ tag_matrix_settled_med }} | {{ tag_matrix_settled_high }} | {{ tag_matrix_settled_med_lift }} | {{ tag_matrix_settled_high_lift }} |
| `judgement` | {{ tag_judgement_rules }} | {{ tag_judgement_med }} | {{ tag_judgement_high }} | {{ tag_judgement_med_lift }} | {{ tag_judgement_high_lift }} |

Cell-run counts per tag are not stored in analysis.json. Bootstrap intervals are approximate (few profiles).
Scope: {{ SCOPE_NOTE }}.

## Effort decision rule (S10P-14)

- **Tier that fired**: Tier {{ effort_tier }} fired.
- **Selected effort**: {{ effort_choice }} effort.
- **Pooled difference (medium − high)**: {{ effort_d_pooled }} (threshold = {{ effort_threshold }}, fraction {{ effort_threshold_fraction }}).
- **Profiles where high <= medium**: {{ effort_high_le_med_count }} of {{ strata_count }} strata profiles (strata profiles with zero gold-mandatory cells = {{ strata_zero_mandatory }}).
- **Non-ok runs failure gap (all {{ dev_profile_count }} profiles, {{ medium_runs }} runs per arm)**: medium non-ok = {{ effort_med_non_ok }}, high non-ok = {{ effort_high_non_ok }} (medium − high = {{ effort_non_ok_gap }}).
- **Paired comparison (high − medium)**: point = {{ paired_point }}, CI = {{ paired_ci }} (approximate (few profiles)).
- **Exact sign-flip test (exact)**: p = {{ sign_flip_p }} ({{ sign_flip_patterns }} patterns).
- **Reporting wording (RS2)**: {{ paired_wording }}.
- **Robustness**: The effort decision holds under all {{ n_versions }} reference versions (main, jury_median, LOJO-A, LOJO-B, LOJO-C, conservative_max); decision_would_change is false for all versions. The chosen effort applies only to the gpt-5-mini configuration at Final.

## Secondary metrics

| Metric | Rules-only | Medium hybrid | High hybrid | Medium increase (hybrid − rules) | High increase (hybrid − rules) |
|---|---|---|---|---|---|
| Over-call rate | {{ sec_rules_over_call }} | {{ sec_med_over_call }} | {{ sec_high_over_call }} | {{ sec_med_over_call_lift }} | {{ sec_high_over_call_lift }} |
| Exact accuracy | {{ sec_rules_acc }} | {{ sec_med_acc }} | {{ sec_high_acc }} | - | - |
| Macro-F1 | {{ sec_rules_f1 }} | {{ sec_med_f1 }} | {{ sec_high_f1 }} | - | - |
| Quadratic weighted kappa (QWK) | {{ sec_rules_qwk }} | {{ sec_med_qwk }} | {{ sec_high_qwk }} | - | - |
| Binary kappa | {{ sec_rules_binkappa }} | {{ sec_med_binkappa }} | {{ sec_high_binkappa }} | - | - |
| Identical profile share | - | {{ sec_med_profile_share }} | {{ sec_high_profile_share }} | - | - |
| Cell flip rate | - | {{ sec_med_cell_flip }} | {{ sec_high_cell_flip }} | - | - |

Bootstrap intervals are approximate (few profiles).
Scope: {{ SCOPE_NOTE }}.

## Why the misses happen (S10c3-05)

### Disagreement table reading
- Medium effort: rules right = {{ disag_med_rules_right }}, LLM right = {{ disag_med_llm_right }}, both wrong = {{ disag_med_both_wrong }} (total n = {{ disag_med_n }}).
- High effort: rules right = {{ disag_high_rules_right }}, LLM right = {{ disag_high_llm_right }}, both wrong = {{ disag_high_both_wrong }} (total n = {{ disag_high_n }}).

### Upgrade precision and recall
- Medium effort overall: precision = {{ up_med_p }}, recall = {{ up_med_r }}.
- High effort overall: precision = {{ up_high_p }}, recall = {{ up_high_r }}.

| Tag | Medium precision | Medium recall | High precision | High recall |
|---|---|---|---|---|
| `always_rule` | {{ tag_always_rule_up_med_p }} | {{ tag_always_rule_up_med_r }} | {{ tag_always_rule_up_high_p }} | {{ tag_always_rule_up_high_r }} |
| `matrix_settled` | {{ tag_matrix_settled_up_med_p }} | {{ tag_matrix_settled_up_med_r }} | {{ tag_matrix_settled_up_high_p }} | {{ tag_matrix_settled_up_high_r }} |
| `judgement` | {{ tag_judgement_up_med_p }} | {{ tag_judgement_up_med_r }} | {{ tag_judgement_up_high_p }} | {{ tag_judgement_up_high_r }} |

Disagreements compare the LLM's own label (before the max(rule, LLM) guard) with the rule label; each is classed by which side matches the reference label. An upgrade is a cell-run whose final label is above the rule label; it is warranted if the reference label is above the rule label (at any level, not only mandatory).

At medium the LLM disagreed with the rules on {{ disag_med_n }} cell-runs ({{ up_med_upgrades }} raised, {{ blocked_downgrades_med }} downgrade blocked by the guard) and was right in {{ disag_med_llm_right }}. At high it disagreed on {{ disag_high_n }} ({{ up_high_upgrades }} raised, {{ blocked_downgrades_high }} blocked) and was right in {{ disag_high_llm_right }}; the rule label was right in {{ disag_high_rules_right }} and neither in {{ disag_high_both_wrong }}. Upgrade precision at high is {{ up_high_p }}, so most raises were not warranted. Recall is {{ up_high_r }}, but a warranted upgrade counts any raise on a cell whose reference label is above the rule, including raises that stop below the reference and cells whose reference is only recommended. So high recall with an under-call reduction of only {{ high_removed_frac }} suggests the LLM often raises labels but rarely to mandatory where the reference says mandatory (INFERENCE from these rows; cell-level reasons were not read).

## Traps (descriptive)

| Metric | Rules-only | Medium hybrid | High hybrid | Medium reduction (rules − hybrid) | High reduction (rules − hybrid) |
|---|---|---|---|---|---|
| Under-call rate | {{ trap_rules_under_call }} | {{ trap_med_under_call }} | {{ trap_high_under_call }} | {{ trap_med_lift }} | {{ trap_high_lift }} |
| Over-call rate | - | {{ trap_med_over_call }} | {{ trap_high_over_call }} | - | - |
| Exact accuracy | - | {{ trap_med_acc }} | {{ trap_high_acc }} | - | - |

Bootstrap intervals are approximate (few profiles). Trap intervals where lo = hi are descriptive only.

## Fixed by construction

Fixed by the reference standard's unanimity; values: {{ fixed_by_construction_values }} (each labelled "identical by construction; not evidence of robustness").

## Not run

- {{ PPI_NOT_RUN_LINE }}
- A3 filler follow-up: skipped by decision on 2026-11-22 (D35).

## Deviations

The original pre-registration and addenda A1–A4 stay unchanged; deviations are listed there (S10 pre-registration addendum A4, Tables A–C, rows 1–19).

## Where it fails / what broke (S10c3-08)

### Method limits
1. Retrieval uses only device_name (contact, material, population ignored).
2. Tags depend on the engine's rule encoding.
3. Profiles drafted by an AI agent, then reviewed and edited by Hariom; the drafting family equals judge A's family.
4. Reference standard = LLM jury + one non-expert human; no regulatory expert; judges' errors are correlated, so their agreement shows agreement, not correctness. The second rater is the only check from outside the AI judges.
5. Robustness to how the reference was built cannot be measured here (all {{ n_versions }} versions identical by construction); the judge-B vendor-family effect (judge B shares the classifier's family) cannot be measured either.
6. ISO 10993-1 2025 edition off: genotoxicity for prolonged contact and some differences not modelled.
7. Not all matrix rows covered; some profiles have no matrix_settled cells.
8. Small n: {{ strata_count }} dev strata profiles; bootstrap intervals descriptive only.
9. Under-call reduction is >= 0 by construction.
10. Trap intervals with lo = hi are descriptive only.

### What broke in the run
- Candidate fallback not recorded in the outputs (PR-49): whether a candidate query fell back is not stored per query (cause not investigated).
- Pilot: one run hit short connection errors before succeeding; diagnosed as rare short network blips (decision D36); no code change; network gate and watchdog added; main had 0 retries.
- The stop rule (3 consecutive error runs, no wait) is fragile to outages of a few seconds (PR-52); retained, with the gate and watchdog, for the Claude rerun and Final.
- Azure Monitor counts each embedding request twice (two operation names); reconciliation uses one name; the ledger stays the enforcement.
- Key and environment issues before main: an old key in a permanent Windows variable and a key shown in a screenshot, then rotated; keys now come only from .env per session.

## What's next

Claude rerun (addendum before Final), then one Final holdout run; holdout still sealed.

## Appendix

### Per-profile table
| Profile ID | Rules under-call | Medium hybrid under-call | High hybrid under-call | Medium reduction | High reduction | Medium U/P | High U/P |
|---|---|---|---|---|---|---|---|
{{ per_profile_table_rows }}

### Leave-one-profile-out tipping ranges
| Omitted profile | Medium U/P | Medium E1 range | Medium E2 range | High U/P | High E1 range | High E2 range |
|---|---|---|---|---|---|---|
{{ leave_one_out_table_rows }}

### Two-way split-cell secondary scoring
No split cell is gold-mandatory (strata split gold-mandatory cells = {{ split_mandatory_count }}); primary two-way scoring is not required.

| Direction | Medium over-call | High over-call | Medium accuracy | High accuracy | Medium macro-F1 | High macro-F1 | Medium QWK | High QWK |
|---|---|---|---|---|---|---|---|---|
{{ two_way_table_rows }}

### Robustness band
| Reference standard version | Medium under-call rate | High under-call rate | Effort decision | Decision changed |
|---|---|---|---|---|
{{ robustness_band_table_rows }}

### Per-judge gaps vs human audit
| Judge | Under-call gap (x/n) | Over-call gap (x/n) |
|---|---|---|
{{ judge_gaps_table_rows }}

### Family overlap
Share of audited strata cell-runs where a judge's label disagrees with the human audit label in which the hybrid's final label equals that judge's label.

Addendum A4 §6 expected this check to be empty because it reasoned from binary (mandatory vs not) agreement; analysis.py compares exact labels, and judge A differs from the human audit below the mandatory line.

| Effort | Judge A | Judge B | Judge C |
|---|---|---|---|
{{ family_overlap_table_rows }}

### Stability detail and blocked downgrades
- Blocked downgrades: medium = {{ blocked_downgrades_med }}, high = {{ blocked_downgrades_high }}.
- Identical profile share: medium = {{ sec_med_profile_share }}, high = {{ sec_high_profile_share }}.
- Cell flip rate: medium = {{ sec_med_cell_flip }}, high = {{ sec_high_cell_flip }}.

### Resources (tokens, latency, attempts)
Scope: sum over {{ strata_runs_per_arm }} strata runs per arm ({{ strata_count }} strata profiles); tokens and latency are not allocated across cell tags.

- **Medium effort (sum over {{ strata_runs_per_arm }} strata runs)**:
  - Tokens: input = {{ tokens_med_input }}, output = {{ tokens_med_output }}, reasoning = {{ tokens_med_reasoning }}, cached = {{ tokens_med_cached }}.
  - Latency: {{ latency_med_s }} seconds ({{ latency_med_per_run_s }} s/run).
  - LLM attempts: {{ attempts_med }}, HTTP attempts: {{ http_attempts_med }}.
- **High effort (sum over {{ strata_runs_per_arm }} strata runs)**:
  - Tokens: input = {{ tokens_high_input }}, output = {{ tokens_high_output }}, reasoning = {{ tokens_high_reasoning }}, cached = {{ tokens_high_cached }}.
  - Latency: {{ latency_high_s }} seconds ({{ latency_high_per_run_s }} s/run).
  - LLM attempts: {{ attempts_high }}, HTTP attempts: {{ http_attempts_high }}.
- **Trap runs latency (sum over {{ trap_runs_per_arm }} trap runs per arm)**: medium = {{ trap_latency_med_s }} seconds ({{ trap_latency_med_per_run_s }} s/run); high = {{ trap_latency_high_s }} seconds ({{ trap_latency_high_per_run_s }} s/run).
