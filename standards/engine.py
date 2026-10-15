"""Pure R9 rules. No files, clocks, databases, model calls, or mutable inputs.

Unrepresentable R9 triggers remain explicit assumptions: v1 has no chemical
release, immune plausibility, or fluid-administration fields. R08 derives
extraction planning from mandatory chemical characterisation or endpoints.
Device-specific standards do not suppress the general rule floor.
"""
from dataclasses import dataclass, field
import json
from typing import TypedDict

from .profile import DeviceProfile

RANK = {'not_applicable': 0, 'recommended': 1, 'mandatory': 2}
CONTACT_2025 = {'intact_skin': 'intact_skin', 'mucosal_membrane': 'intact_mucosal',
                'breached_compromised_surface': 'breached_or_internal_tissue',
                'tissue_bone_dentin': 'breached_or_internal_tissue', 'tissue_bone': 'breached_or_internal_tissue',
                'blood_path_indirect': 'circulating_blood', 'circulating_blood': 'circulating_blood', 'blood': 'circulating_blood'}


class RuleResult(TypedDict):
    parts: list[dict]
    notes: list[str]
    assumptions: list[str]
    framework_row_unverified: bool


@dataclass
class Context:
    profile: DeviceProfile
    component: object
    data: dict
    endpoints: dict = field(default_factory=dict)
    traces: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    duration: str = 'C'
    unverified: bool = False
    anchor: bool = False
    suggested_route: str | None = None
    parts: list = field(default_factory=list)

    def set(self, endpoint, label, rule):
        old = self.endpoints.get(endpoint, 'not_applicable')
        if RANK[label] >= RANK[old]:
            self.endpoints[endpoint] = label
        self.traces.setdefault(endpoint, set()).add(f'R{rule:02d}')


def rule_01(c):
    return c.component.contact_nature == 'non_contact'


def rule_02(c):
    e = c.component.exposure
    if c.profile.framework == 'ISO_10993_1_2025':
        hours = e.contact_days * 24 if e.contact_days is not None else None
    elif e.pattern in {'single', 'continuous'}:
        hours = e.hours_per_use
    else:
        hours = e.hours_per_use * e.uses_per_patient if e.hours_per_use is not None and e.uses_per_patient is not None else None
    if hours is None:
        c.duration = 'C'
        c.assumptions.append(f'{c.component.name}: R02 missing exposure; longer class C assumed.')
    else:
        c.duration = 'A' if hours <= 24 else 'B' if hours <= 720 else 'C'
    if e.reasonably_foreseeable_misuse:
        c.notes.append(f'{c.component.name}: R02 assess reasonably foreseeable misuse: {e.reasonably_foreseeable_misuse}')


def rule_03(c):
    comp = c.component
    if c.profile.framework == 'ISO_10993_1_2025':
        if comp.contact_site == 'blood_path_indirect':
            c.unverified = True
            c.assumptions.append(f'{comp.name}: R03 indirect blood-path mapping to 2025 is UNVERIFIED; FDA fallback.')
        else:
            rows = [r for r in c.data['anchors']['rows'] if r['contact'] == CONTACT_2025[comp.contact_site]
                    and r['duration'] == c.duration and (r['nature'] is None or r['nature'] == comp.contact_nature)]
            if rows:
                c.anchor = True
                for ep in rows[0]['endpoints']:
                    c.set(ep, 'mandatory', 3)
                return
        c.unverified = True
        c.assumptions.append(f'{comp.name}: R03 2025 row unverified; FDA row used.')
    rows = [r for r in c.data['matrix']['rows'] if (r['nature'], r['site'], r['duration']) ==
            (comp.contact_nature, comp.contact_site, c.duration)]
    if not rows:
        # R9's JSON schema permits combinations absent from its matrix.
        c.unverified = True
        rows = [r for r in c.data['matrix']['rows'] if r['duration'] == c.duration]
        c.assumptions.append(f'{comp.name}: R03 contact combination absent from matrix; conservative union at duration {c.duration}.')
    for row in rows:
        for ep, cell in row['cells'].items():
            if cell['consider']:
                c.set(ep, 'mandatory', 3)


def rule_04(c):
    comp = c.component
    if comp.contact_nature == 'externally_communicating' and comp.contact_site == 'circulating_blood' and c.duration == 'A':
        if c.anchor:
            # §B 2025 anchor explicitly includes Geno without the FDA footnote.
            c.assumptions.append(f'{comp.name}: R04 FDA extracorporeal footnote does not override the 2025 anchor.')
        else:
            c.endpoints['Geno'] = 'mandatory' if comp.extracorporeal_circuit else 'not_applicable'
            c.traces.setdefault('Geno', set()).add('R04')


def rule_05(c):
    comp = c.component
    if comp.contact_site != 'intact_skin':
        c.set('Repro', 'recommended', 5)
        c.set('Degradation', 'recommended', 5)
    degrading = any(m.absorbable_or_degradable or m.in_situ_polymerizing for m in comp.materials)
    if degrading:
        c.set('Degradation', 'mandatory', 5)
    label = c.endpoints.get('Degradation', 'not_applicable')
    for material in comp.materials:
        ep = {'polymer': 'DegradationPolymer', 'hydrogel_adhesive': 'DegradationPolymer',
              'metal_alloy': 'DegradationMetal', 'ceramic': 'DegradationCeramic'}.get(material.material_class)
        if ep:
            c.set(ep, label, 5)
    if comp.reproductive_tissue_contact:
        c.notes.append(f'{comp.name}: R05 reproductive tissue contact warrants specific reproductive/developmental assessment.')


def rule_06(c):
    comp = c.component
    if c.endpoints.get('Hemo') == 'mandatory':
        indirect = comp.contact_mode == 'indirect' or comp.contact_site == 'blood_path_indirect'
        detail = 'hemolysis only; review chemical release into blood (not represented in v1)' if indirect else 'hemolysis, complement, thrombogenicity'
        if indirect and any(m.novel for m in comp.materials):
            detail = 'hemolysis, complement, thrombogenicity; novel material broadens indirect-contact review'
        c.notes.append(f'{comp.name}: R06 {detail}.')
        if indirect:
            c.assumptions.append(f'{comp.name}: R06 chemical release into blood is not represented in v1; if present, broaden haemocompatibility beyond hemolysis.')
        c.traces.setdefault('Hemo', set()).add('R06')


def rule_07(c):
    comp = c.component
    blood = comp.contact_site in {'blood', 'circulating_blood', 'blood_path_indirect'}
    trigger = (comp.contact_nature == 'implant' or c.profile.labeled_non_pyrogenic or
               (comp.sterilization != 'non_sterile' and (comp.contacts_csf_lymph_or_cardiovascular or blood)))
    if trigger:
        c.set('MMP', 'mandatory', 7)
        c.notes.append(f'{comp.name}: R07 bacterial endotoxin evaluation is outside the ISO 10993 series (ISO 11737-3).')
        higher_risk = c.profile.labeled_non_pyrogenic or any(m.novel or m.absorbable_or_degradable or m.material_class == 'biological_derived' for m in comp.materials)
        c.notes.append(f'{comp.name}: R07 ' + ('Material rationale alone needs additional justification; review material history.' if higher_risk else 'MMP may be met by a material rationale.'))


def rule_08(c):
    """R08: -1, -17, -18 mandatory for any contacting component; -12 mandatory
    if any extract-based test or chemical extraction is planned, else recommended.

    D1 derives planned extraction per component: mandatory -18 (always set by
    R08; chemical characterisation uses extraction), OR a mandatory extract-based
    endpoint: cytotoxicity, sensitisation, irritation, acute/sub-acute/chronic
    systemic toxicity, genotoxicity, implantation, haemocompatibility tested on
    extracts (Cyto, Sens, Irr, AST, SubA, Chr, Geno, Impl, Hemo). No new profile
    field is needed. Thus every contacting component currently makes -12
    mandatory; the else branch remains recommended if neither trigger applies.
    """
    for ep in ['Framework', 'TRA', 'Chemical']:
        c.set(ep, 'mandatory', 8)
    extract_endpoints = ['Cyto', 'Sens', 'Irr', 'AST', 'SubA', 'Chr', 'Geno', 'Impl', 'Hemo']
    chemical = c.endpoints.get('Chemical') == 'mandatory'
    mandatory_extracts = [ep for ep in extract_endpoints if c.endpoints.get(ep) == 'mandatory']
    planned = chemical or bool(mandatory_extracts)
    c.set('Sample', 'mandatory' if planned else 'recommended', 8)
    c.traces['Sample'].add(
        f'R08: -12 mandatory if any extract-based test or chemical extraction is planned, else recommended; '
        f'derived per component {c.component.name}: -18 mandatory={chemical} '
        f'(R08 always makes -18 mandatory; chemical characterisation uses extraction), '
        f'OR any mandatory extract-based endpoint (cytotoxicity, sensitisation, irritation, '
        f'acute/sub-acute/chronic systemic toxicity, genotoxicity, implantation, '
        f'haemocompatibility tested on extracts); mandatory endpoints={mandatory_extracts}; '
        f'extraction planned={planned}.')


def rule_09(c):
    eo = c.component.sterilization == 'ethylene_oxide'
    c.set('EO', 'mandatory' if eo else 'not_applicable', 9)
    if eo and set(c.profile.intended_population) & {'neonate_infant', 'pregnant'}:
        c.notes.append(f'{c.component.name}: R09 flag ISO 10993-7:2026 risk-based limits for neonates/infants or pregnant patients.')


def rule_10(c):
    for entry in c.data['part_map']['parts']:
        endpoints = {ep: c.endpoints.get(ep, 'not_applicable') for ep in entry['endpoints']}
        traces = {r for ep in entry['endpoints'] for r in c.traces.get(ep, set())}
        traces.update({'R02', 'R10'})
        edition = entry['edition']
        if entry['part'] == 'ISO 10993-1' and c.profile.framework != 'ISO_10993_1_2025':
            edition = '2018 (FDA accepts until 1 Jul 2029 per R9)'
        c.parts.append(dict(part=entry['part'], edition=edition, label=max(endpoints.values(), key=RANK.get),
                            endpoints=endpoints, rule_trace=sorted(traces), suggested_route=c.suggested_route))


def rule_11(c):
    if c.component.contact_site == 'intact_skin' and any(m.material_class in {'polymer', 'hydrogel_adhesive'} for m in c.component.materials):
        c.notes.append(f'{c.component.name}: R11 Attachment G may reduce information for common polymers/fabrics; establish material eligibility. Labels are retained.')


def rule_12(c):
    if any(m.novel or m.submicron_or_nano for m in c.component.materials):
        c.notes.append(f'{c.component.name}: R12 evaluations beyond the matrix may be needed; consider ISO/TS 10993-20 if immune effects are plausible.')
        c.assumptions.append(f'{c.component.name}: R12 immune-effect plausibility cannot be expressed in v1; no automatic -20 trigger.')


def rule_13(c):
    evidence = c.component.existing_evidence
    if evidence and any(evidence.model_dump().values()):
        c.suggested_route = 'leverage existing data'
        for p in c.parts:
            p['suggested_route'] = c.suggested_route
            p['rule_trace'] = sorted(set(p['rule_trace']) | {'R13'})


def rule_14(c):
    if c.profile.device_specific_standards:
        c.notes.append('R14 Follow the more specific device standard; device-specific standards are listed first.')


def rule_15(c):
    if 'EU' in c.profile.target_markets:
        if c.component.contact_nature in {'implant', 'externally_communicating'} or c.component.contact_site == 'breached_compromised_surface':
            c.notes.append(f'{c.component.name}: R15 MDR Annex I §10.4.1: justify >0.1% w/w CMR 1A/1B and endocrine-disrupting substances; served by ISO 10993-18.')
        c.assumptions.append(f'{c.component.name}: R15 v1 has no explicit invasiveness or medicine/body-fluid administration field; review other §10.4.1 triggers.')


def rule_16(c):
    for p in c.parts:
        p['needs_citation'] = True
        p['rule_trace'] = sorted(set(p['rule_trace']) | {'R16'})


RULES = [rule_02, rule_03, rule_04, rule_05, rule_06, rule_07, rule_08, rule_09,
         rule_10, rule_11, rule_12, rule_13, rule_14, rule_15, rule_16]


def evaluate(profile: DeviceProfile | dict, data: dict) -> RuleResult:
    profile = profile if isinstance(profile, DeviceProfile) else DeviceProfile.model_validate(profile)
    contexts = []
    for comp in sorted(profile.components, key=lambda x: json.dumps(x.model_dump(), sort_keys=True)):
        c = Context(profile, comp, data)
        if rule_01(c):
            rule_10(c)
            for part in c.parts:
                part['rule_trace'] = ['R01', 'R10']
            c.notes.append(f'{comp.name}: R01 no-contact statement only.')
            rule_16(c)
        else:
            for rule in RULES:
                rule(c)
        contexts.append(c)
    merged = {}
    for c in contexts:
        for part in c.parts:
            if part['part'] not in merged:
                merged[part['part']] = {**part, 'endpoints': dict(part['endpoints']), 'rule_trace': list(part['rule_trace'])}
            else:
                old = merged[part['part']]
                old['label'] = max([old['label'], part['label']], key=RANK.get)
                old['rule_trace'] = sorted(set(old['rule_trace']) | set(part['rule_trace']))
                old['suggested_route'] = old['suggested_route'] or part['suggested_route']
                for ep, label in part['endpoints'].items():
                    old['endpoints'][ep] = max([old['endpoints'][ep], label], key=RANK.get)
    contacting = any(c.contact_nature != 'non_contact' for c in profile.components)
    specifics = []
    for name in dict.fromkeys(profile.device_specific_standards):
        if name in merged:
            part = merged.pop(name)
            part['label'] = 'mandatory' if contacting else 'not_applicable'
            part['rule_trace'] = sorted(set(part['rule_trace']) | {'R14'})
        else:
            part = dict(part=name, edition='UNVERIFIED', label='mandatory' if contacting else 'not_applicable',
                        endpoints={}, rule_trace=['R14', 'R16'], suggested_route=None, needs_citation=True)
        specifics.append(part)
    return dict(parts=specifics + [merged[k] for k in sorted(merged)],
                notes=sorted({n for c in contexts for n in c.notes}),
                assumptions=sorted({a for c in contexts for a in c.assumptions}),
                framework_row_unverified=any(c.unverified for c in contexts))
