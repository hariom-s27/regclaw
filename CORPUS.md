# Corpus provenance and licensing

There are no PDFs in this repo.

## Licence conditions

| Source | Status | Basis | In repo? |
|---|---|---|---|
| FDA web content | Not copyrighted, public domain unless noted; credit appreciated; FDA asks copiers to record the copy date and link back | FDA Website Policies https://www.fda.gov/about-fda/about-website/website-policies | No (download script) |
| US federal works incl. CFR/eCFR | 17 U.S.C. §105: no copyright for US Government works. Applying this to CFR text is a reading of §105 → UNVERIFIED | https://www.law.cornell.edu/uscode/text/17/105 | No |
| EU Commission content | Decision 2011/833/EU; CC BY 4.0 unless indicated; third-party content excluded | https://commission.europa.eu/legal-notice_en | No |
| EU MDR text on EUR-Lex | EUR-Lex licence specifically UNVERIFIED | — | No |
| WHO publications | CC BY-NC-SA 3.0 IGO | https://www.who.int/about/policies/publishing/copyright | No |
| IMDRF documents | © IMDRF; non-commercial personal/research/education use; that every doc uses the same notice is UNVERIFIED | IMDRF copyright notice | No |
| CDSCO (India) | May not be reproduced without permission; acknowledge source | https://cdsco.gov.in/opencms/opencms/en/Copyright-Policy/ | No |
| ISO / IEC standards | Copyrighted, sold commercially | — | Never |

## Why no PDFs

The corpus mixes public-domain US material with documents under non-commercial, share-alike, or permission-required terms (WHO, IMDRF, CDSCO). Redistributing those in this repository would conflict with their terms, and some PDFs embed third-party material with separate rights. The repo therefore ships only code and the scripts/manifests needed to download each document from its official URL for local use.

## Document inventory

Codes and optional status are configured in [ingest/sources.yaml](ingest/sources.yaml).
Acquisition and hashing are implemented in [corpus/download.py](corpus/download.py);
edition metadata is in [graph/standards.yaml](graph/standards.yaml).
The recorded acquisition digests below identify the locally acquired bytes,
not a guarantee about future downloads. No issuer URL was fetched in this review.

| Document | Source URL | Version/date | Recorded SHA256 | Licence status | In repo |
|---|---|---|---|---|---|
| `fda-iso10993-1` | [source](https://www.fda.gov/media/142959/download) | September 2023 final | `785a0c14ccd4471b5aecae34678327fa6bef29b3230653e910b7baf9188689eb` | FDA public domain unless noted; credit and copy date requested | no |
| `fda-sterility-510k` | [source](https://www.fda.gov/media/74445/download) | January 2024 final | `a9d669c3d4584c48799b63f16289894374986a4639ff325bb802f449a232ab58` | FDA public domain unless noted; credit and copy date requested | no |
| `fda-chem-analysis` | [source](https://www.fda.gov/media/181952/download) | September 2024 draft | `da523cd37dd234e4c64ebb410f796a9272002f77143684410f742e6f32cac466` | FDA public domain unless noted; credit and copy date requested | no |
| `fda-nitinol` | [source](https://fda.gov/media/123272/download) | July 2021 | `e962a33542c2596136c001c8e633845a4866f674676f65d7b7e366b3afd51ca7` | FDA public domain unless noted; credit and copy date requested | no |
| `fda-knee` | [source](https://www.fda.gov/medical-devices/guidance-documents-medical-devices-and-radiation-emitting-products/knee-joint-patellofemorotibial-and-femorotibial-metalpolymer-porous-coated-uncemented-prostheses) | 2003 HTML print | `07b7dd562b565c5c66e1e3db2ff23635afaff580f052c670acd0803bd53645c9` | FDA public domain unless noted; credit and copy date requested | no |
| `eu-mdr` | [source](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32017R0745) | Original OJ 2017 text; local slice | `71f1b6b6d69def534110691aa109bb17a5c3a2369dc2e1ecd3d2e19e50496db9` | EUR-Lex-specific licence UNVERIFIED | no |
| `imdrf-n47` | [source](https://www.pmda.go.jp/files/000269646.pdf) | 2018 | `88c238c5237184a25e335181d8300c264e919ac02fcfec823869ed9b24b2bf17` | IMDRF: non-commercial personal/research/education use; notice uniformity UNVERIFIED | no |
| `in-ep` | [source](https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadPublic_NoticesFiles/Essential_Principles_for_safety_47.pdf) | July 2017 draft | `351e74d876435e3ca5d795236af724743b6e92bbffddaa5ae6ccaac888b7c816` | CDSCO: permission required; acknowledge source | no |
| `who-decon` | [source](https://iris.who.int/server/api/core/bitstreams/d128e952-f919-4462-bdb0-392312908782/content) | 2016; optional, disabled | `8497b186bbb6f91d6ac4d26a5977276a569b672577b5a12a0273821d2e1853bf` | WHO: CC BY-NC-SA 3.0 IGO | no |
| `in-mdr` | [source](https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2022/m_device/mdr,%202017%20(1).pdf) | 2017 rules, compilation through October 2022 | `0839b29f70af4d376e3fa1f11d5d11c9e0b62e36f9aa93e555f618753d28c4d0` | CDSCO: permission required; acknowledge source | no |
| `epa-63o` | [source](https://www.ecfr.gov/current/title-40/chapter-I/subchapter-C/part-63/subpart-O) | eCFR as of 2026-10-06; source rule 2024 | `460536c85f324e366650fcea116cf45bb90a46083b7cdb3c392a9ac88ee7c317` | US federal work; application of section 105 UNVERIFIED | no |

## Acquisition details

The EU acquisition digest identifies the complete source. The ingester consumes
a slice defined by [corpus/eu_mdr_slices.yaml](corpus/eu_mdr_slices.yaml).
The locally measured slice SHA256 is
`bf0f768b5359802ce45a919009198626f55d8a5ea87becff3a99d4d98909165e`.
It was checked on 2026-10-16; slicing and digest implementation are in
[corpus/download.py](corpus/download.py). The full-source digest is in the table.
The knee source is an HTML print of the linked official page.
The optional WHO source is configured but disabled by default.
