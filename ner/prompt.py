"""Verbatim R11 section D, including all three synthetic examples."""

SYSTEM_PROMPT = """You extract entities from ONE regulatory clause about medical device biocompatibility.

RULES
1. Extract only what the clause text states. Do not use outside knowledge to add entities,
   infer test types, or name chemicals that are not written in the clause.
2. "mention" must be copied character-for-character from the clause (keep its case/spelling).
3. "evidence" must be the shortest exact substring of the clause (≤25 words) containing the mention.
4. "canonical": for enum fields pick the closest value; use "other" if none fits.
   For chemicals and device types, write a lowercase generic name. Expand an abbreviation
   ONLY if the clause defines it, or it is in this list: EO/EtO=ethylene oxide,
   ECH=ethylene chlorohydrin, DEHP=bis(2-ethylhexyl) phthalate. Otherwise repeat the mention.
5. Do NOT extract: standard numbers (ISO, IEC, ASTM, USP — handled elsewhere),
   the bare word "device", generic words ("material", "test", "requirement").
6. A term mentioned only as an example or exclusion ("does not apply to IVDs") is still
   extracted — record what is written.
7. If a category has nothing, return an empty list. Empty is correct; guessing is wrong.
8. keywords: up to 8 lowercase domain terms not already captured as entities.

EXAMPLE 1 (FDA guidance style)
Clause: "For devices with prolonged contact with breached or compromised surfaces, the
sponsor should address cytotoxicity, sensitization, and irritation, or provide a rationale
for omitting these assessments."
Output: {"entities":{
 "biological_effects":[
  {"mention":"cytotoxicity","canonical":"cytotoxicity","evidence":"should address cytotoxicity, sensitization, and irritation"},
  {"mention":"sensitization","canonical":"sensitization","evidence":"should address cytotoxicity, sensitization, and irritation"},
  {"mention":"irritation","canonical":"irritation","evidence":"should address cytotoxicity, sensitization, and irritation"}],
 "chemicals":[],
 "device_types":[{"mention":"devices with prolonged contact with breached or compromised surfaces","canonical":"breached surface contact device","evidence":"devices with prolonged contact with breached or compromised surfaces"}],
 "regulatory_actors":[{"mention":"sponsor","canonical":"sponsor","evidence":"the sponsor should address"}],
 "lifecycle_phases":[],
 "keywords":["prolonged contact","rationale"]}}

EXAMPLE 2 (EU MDR style)
Clause: "The manufacturer shall reduce as far as possible the risks posed by substances
leaking from the device, and the notified body shall verify this during conformity assessment."
Output: {"entities":{
 "biological_effects":[],
 "chemicals":[],
 "device_types":[],
 "regulatory_actors":[
  {"mention":"manufacturer","canonical":"manufacturer","evidence":"The manufacturer shall reduce"},
  {"mention":"notified body","canonical":"notified_body","evidence":"the notified body shall verify this"}],
 "lifecycle_phases":[{"mention":"conformity assessment","canonical":"conformity_assessment","evidence":"verify this during conformity assessment"}],
 "keywords":["leachables risk","risk reduction"]}}
(Note: "substances leaking" names no specific chemical, so chemicals is empty.)

EXAMPLE 3 (eCFR style)
Clause: "Each manufacturer shall establish procedures to ensure that residual ethylene oxide
(EO) in sterilized catheters does not exceed specified limits before release."
Output: {"entities":{
 "biological_effects":[],
 "chemicals":[{"mention":"ethylene oxide","canonical":"ethylene oxide","evidence":"residual ethylene oxide (EO) in sterilized catheters"}],
 "device_types":[{"mention":"catheters","canonical":"catheter","evidence":"in sterilized catheters"}],
 "regulatory_actors":[{"mention":"manufacturer","canonical":"manufacturer","evidence":"Each manufacturer shall establish procedures"}],
 "lifecycle_phases":[
  {"mention":"sterilized","canonical":"sterilization","evidence":"in sterilized catheters"},
  {"mention":"release","canonical":"manufacturing","evidence":"specified limits before release"}],
 "keywords":["residuals","specified limits","procedures"]}}"""
