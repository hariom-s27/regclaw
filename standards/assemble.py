"""Assemble the response without hiding rule/data/model provenance."""
from datetime import datetime, timezone
from . import ENGINE_VERSION
from .classifier import PROMPT_SHA256
from .overclaim import lint_output
from .ruleset import ENGINE_SHA256, RULESET_SHA256, RULESET_VERSION, ruleset_for


def assemble(profile, data, rules, guarded, model, *, generated_at=None):
    standards = []
    for row in guarded['standards']:
        entry = {**row, 'framework_row_unverified': rules['framework_row_unverified']}
        entry['edition_unverified'] = entry['edition'] == 'UNVERIFIED'
        standards.append(entry)
    ruleset, disclaimer = ruleset_for(profile.framework, rules['framework_row_unverified'])
    result = dict(device_name=profile.device_name, framework=profile.framework,
                  ruleset=ruleset, ruleset_version=RULESET_VERSION, ruleset_sha256=RULESET_SHA256,
                  disclaimer=disclaimer,
                  standards=standards, notes=guarded['notes'], assumptions=rules['assumptions'],
                  caveat=data['labels']['caveat'],
                  provenance=dict(engine_version=ENGINE_VERSION, engine_sha256=ENGINE_SHA256,
                      data_version=data['data_version'],
                      model=model, prompt_sha256=PROMPT_SHA256, llm_status=guarded['llm_status'],
                      disagreements=guarded['disagreements'],
                      generated_at=generated_at or datetime.now(timezone.utc).isoformat()))
    # Residual over-claims after the guard (engine notes, caveat, templates): expected [].
    result['provenance']['overclaim_hits'] = lint_output(result, data['labels']['banned_phrases'])
    return result
