"""Assemble the response without hiding rule/data/model provenance."""
from datetime import datetime, timezone
from . import ENGINE_VERSION
from .classifier import PROMPT_SHA256


def assemble(profile, data, rules, guarded, model, *, generated_at=None):
    standards = []
    for row in guarded['standards']:
        entry = {**row, 'framework_row_unverified': rules['framework_row_unverified']}
        entry['edition_unverified'] = entry['edition'] == 'UNVERIFIED'
        standards.append(entry)
    return dict(device_name=profile.device_name, framework=profile.framework,
                standards=standards, notes=guarded['notes'], caveat=data['labels']['caveat'],
                provenance=dict(engine_version=ENGINE_VERSION, data_version=data['data_version'],
                    model=model, prompt_sha256=PROMPT_SHA256, llm_status=guarded['llm_status'],
                    disagreements=guarded['disagreements'],
                    generated_at=generated_at or datetime.now(timezone.utc).isoformat()))
