# ATLAS

**Automated Test Learning & Analytics System**

ATLAS is an internal semiconductor FT/Wafer data analysis toolkit.

## Version

Current version:

v1.1.0

## Purpose

ATLAS supports semiconductor FT and wafer data analysis, including data preprocessing, wafer-level merging, yield summary, BIN analysis, test item summary, map generation, and probability plotting.

## Current Functions

- FT data preprocessing
- Wafer data merge and cleaning
- Yield and failure-rate summary
- BIN summary
- Test item mean summary
- BIN Map
- Test Map
- Probability Plot
- Config-driven Product Profiles for FT filename parsing
- Mandatory Preview and validation before filename normalization

## Product Profiles

FT naming rules are stored in `config/products.yaml`. Each enabled product must
define a Regex with named `lot` and `wafer` capture groups. The bundled entries
are synthetic examples and should be replaced or extended only with approved,
non-sensitive product configuration.

## Entry Point

Run:

```bash
python main.py
```

## Data Policy

Real company FT data must not be committed to this repository.

Raw data, production data, generated reports, generated maps, and local experiment outputs should be stored outside the source-code repository.

## Version Baseline

v1.0.0 is the first Git-managed stable baseline of ATLAS.
