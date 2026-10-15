"""E1--E4, using an explicitly empty fake retriever and FakeLLM only."""
import json
from .classifier import FakeLLM
from .tool import get_applicable_standards


class FakeRetriever:
    def __init__(self, candidates=()):
        self.candidates = list(candidates)
        self.requests = []

    def retrieve(self, query, top_k=30):
        self.requests.append((query, top_k))
        return self.candidates[:top_k], None


def example_profiles():
    def profile(name, nature, site, hours, materials, sterilization, **extras):
        return dict(device_name=name, components=[dict(name=name, contact_nature=nature, contact_site=site,
            exposure=dict(pattern='single', hours_per_use=hours),
            materials=[dict(name=n, material_class=k) for n, k in materials], sterilization=sterilization)], **extras)
    out = {
        'E1': profile('Total knee implant', 'implant', 'tissue_bone', 8760,
                      [('CoCr', 'metal_alloy'), ('Ti alloy', 'metal_alloy'), ('UHMWPE', 'polymer')], 'gamma'),
        'E2': profile('Skin electrode', 'surface', 'intact_skin', 24,
                      [('Hydrogel adhesive', 'hydrogel_adhesive'), ('Polymer backing', 'polymer')], 'non_sterile'),
        'E3': profile('Central venous catheter', 'externally_communicating', 'circulating_blood', 1440,
                      [('Polyurethane', 'polymer')], 'ethylene_oxide'),
        'E4': profile('Contact lens', 'surface', 'mucosal_membrane', 1000,
                      [('Silicone hydrogel', 'hydrogel_adhesive')], 'steam', device_specific_standards=['ISO 9394'])}
    out['E3']['components'][0]['extracorporeal_circuit'] = False
    out['E4']['components'][0]['exposure'] = dict(pattern='intermittent_repeat', hours_per_use=10, uses_per_patient=100)
    return out


def main(argv=None):
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    for key, profile in example_profiles().items():
        result = get_applicable_standards(profile, FakeRetriever(), llm=FakeLLM())
        print(key, json.dumps(result, ensure_ascii=False, indent=2))
    return 0
