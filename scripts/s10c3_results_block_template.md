{{ HEADER }}

## S10c provisional (GPT): dev run results

- **Plain summary**:
  - What we tested: whether the LLM layer of `get_applicable_standards` (final = max(rule, LLM)) catches mandatory ISO 10993 parts that the rules miss. Dev set: {{ strata_count }} strata profiles ({{ trap_count }} trap profiles reported separately), {{ n_efforts }} efforts × {{ repeats }} repeats; classifier Azure gpt-5-mini; provisional (GPT), dev only.
  - The tool still misses mandatory parts: under-call U/P = {{ primary_medium_under_call }} at medium (tipping range at E1: {{ tipping_medium_e1 }}; at E2: {{ tipping_medium_e2 }}) and {{ primary_high_under_call }} at high (tipping range at E1: {{ tipping_high_e1 }}; at E2: {{ tipping_high_e2 }}), against {{ primary_rules_under_call }} for the rules alone.
  - The misses sit in judgement items: rules-only under-call is {{ tag_judgement_rules }} on judgement items and {{ tag_matrix_settled_rules }} on matrix_settled and {{ tag_always_rule_rules }} on always_rule items.
  - The LLM barely helps and adds over-calls: it removed {{ med_removed_frac }} misses at medium and {{ high_removed_frac }} at high (a reduction of 0 or more is guaranteed by design). Paired difference high − medium = {{ paired_difference }} (approximate (few profiles)), {{ paired_wording }}; exact sign-flip p = {{ sign_flip_p }}. Over-call rises by {{ sec_med_over_call_lift }} at medium and {{ sec_high_over_call_lift }} at high (approximate (few profiles) intervals).
  - Effort rule (S10P-14): tier {{ effort_tier }} → {{ effort_choice }}, the same under all {{ n_versions }} reference versions. The holdout has not been run; it stays sealed until Final, after a Claude rerun.

- **Primary and secondary summary**:

| Metric | Rules-only | Medium hybrid | High hybrid | Reduction / Increase (rules − hybrid / hybrid − rules) |
|---|---|---|---|---|
| Under-call rate | {{ primary_medium_rules_under_call }} | {{ primary_medium_under_call }} | {{ primary_high_under_call }} | Medium: {{ lift_medium_reduction }} {{ lift_medium_ci }}; High: {{ lift_high_reduction }} {{ lift_high_ci }} |
| Over-call rate | {{ sec_rules_over_call }} | {{ sec_med_over_call }} | {{ sec_high_over_call }} | Medium: {{ sec_med_over_call_lift }}; High: {{ sec_high_over_call_lift }} |
| Exact accuracy | {{ sec_rules_acc }} | {{ sec_med_acc }} | {{ sec_high_acc }} | - |
| Macro-F1 | {{ sec_rules_f1 }} | {{ sec_med_f1 }} | {{ sec_high_f1 }} | - |
| Quadratic weighted kappa (QWK) | {{ sec_rules_qwk }} | {{ sec_med_qwk }} | {{ sec_high_qwk }} | - |
| Binary kappa | {{ sec_rules_binkappa }} | {{ sec_med_binkappa }} | {{ sec_high_binkappa }} | - |

Bootstrap intervals are approximate (few profiles).

{{ STRUCTURAL_NOTE }}

Scope: {{ SCOPE_NOTE }}.

- **Effort decision (S10P-14)**: Tier {{ effort_tier }} fired, selecting {{ effort_choice }} effort. Paired difference (high − medium) = {{ paired_difference }} (approximate (few profiles); exact sign-flip p = {{ sign_flip_p }}). Holds across all {{ n_versions }} reference standard versions. Applies only to the gpt-5-mini configuration at Final.

- **Run health & accounting**: Completed {{ total_runs }} runs ({{ medium_runs }} medium, {{ high_runs }} high), {{ main_retries }} retries; {{ llm_detail_medium_non_ok }} non-ok at medium and {{ llm_detail_high_non_ok }} non-ok at high across all {{ medium_runs }} runs per arm. Paid requests on global ledger: candidates = {{ candidates_paid_requests }}, pilot = {{ pilot_paid_requests }}, main = {{ main_paid_requests }}; total = {{ total_paid_requests }} against cap {{ paid_call_cap }}. {{ AZURE_RECONCILIATION }}

- **Full details**: Complete dev results, disagreement tables, tag splits, and sensitivity tables are in [results_s10c_dev.md](results_s10c_dev.md).
