"""Exact DeviceProfile.v1 fields from R9 §A; derived values are engine-owned."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Framework = Literal['FDA_2023_ATTACHMENT_A', 'ISO_10993_1_2018', 'ISO_10993_1_2025']
Site = Literal['intact_skin', 'mucosal_membrane', 'breached_compromised_surface',
               'blood_path_indirect', 'tissue_bone_dentin', 'circulating_blood', 'tissue_bone', 'blood']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)

    @model_validator(mode='before')
    @classmethod
    def no_explicit_nulls(cls, values):
        if isinstance(values, dict) and any(v is None for v in values.values()):
            raise ValueError('R9 optional fields may be omitted but are not nullable')
        return values


class Exposure(StrictModel):
    pattern: Literal['single', 'continuous', 'intermittent_repeat']
    hours_per_use: float | None = Field(default=None, ge=0)
    uses_per_patient: int | None = Field(default=None, ge=1)
    contact_days: int | None = Field(default=None, ge=0)
    total_exposure_period_days: float | None = None
    reasonably_foreseeable_misuse: str | None = None


class Material(StrictModel):
    name: str
    material_class: Literal['polymer', 'metal_alloy', 'ceramic', 'biological_derived', 'hydrogel_adhesive', 'other'] | None = None
    novel: bool | None = None
    absorbable_or_degradable: bool | None = None
    in_situ_polymerizing: bool | None = None
    submicron_or_nano: bool | None = None
    conforms_to_material_standard: str | None = None
    master_file: str | None = None


class ExistingEvidence(StrictModel):
    equivalent_marketed_device: bool | None = None
    chemical_characterization_iso_10993_18: bool | None = None
    toxicological_risk_assessment_iso_10993_17: bool | None = None


class Derived(StrictModel):
    duration_class: Literal['A_limited', 'B_prolonged', 'C_long_term'] | None = None
    iso2025_contact_type: Literal['intact_skin', 'intact_mucosal', 'breached_or_internal_tissue', 'circulating_blood'] | None = None


class Component(StrictModel):
    name: str
    contact_nature: Literal['non_contact', 'surface', 'externally_communicating', 'implant']
    contact_site: Site
    contact_mode: Literal['direct', 'indirect'] | None = None
    contacts_csf_lymph_or_cardiovascular: bool | None = None
    extracorporeal_circuit: bool | None = None
    reproductive_tissue_contact: bool | None = None
    exposure: Exposure
    materials: list[Material] = Field(min_length=1)
    sterilization: Literal['non_sterile', 'ethylene_oxide', 'gamma', 'e_beam', 'steam', 'vhp', 'other']
    existing_evidence: ExistingEvidence | None = None
    derived: Derived | None = None

    @model_validator(mode='before')
    @classmethod
    def read_only_derived(cls, values):
        if isinstance(values, dict) and 'derived' in values:
            raise ValueError('derived is readOnly; the engine computes it')
        return values


class DeviceProfile(StrictModel):
    device_name: str
    framework: Framework = 'FDA_2023_ATTACHMENT_A'
    target_markets: list[Literal['US', 'EU', 'JP', 'IN', 'OTHER']] = Field(default_factory=list)
    intended_population: list[Literal['adult', 'pediatric', 'neonate_infant', 'pregnant', 'immunocompromised', 'limited_life_expectancy']] = Field(default_factory=list)
    labeled_non_pyrogenic: bool = False
    device_specific_standards: list[str] = Field(default_factory=list)
    components: list[Component] = Field(min_length=1)
