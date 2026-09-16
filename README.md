# ATLAS

**Automated Test Learning & Analytics System**

ATLAS is an internal semiconductor FT/Wafer data analysis toolkit.

## Version

Current version:

v1.4.0

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
- Incremental root-folder cleaning and three cumulative product-level yield reports
- Direct cleaned-CSV plotting with Product/Lot/Wafer selection and shared bounded cache

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

Use the **自动增量良率** tab for root-folder processing. See
[the incremental pipeline guide](docs/incremental_pipeline.md) for directory layout,
product configuration, die-count checks, recovery, and CLI usage.

The BIN Map, Test Map, and Probability tabs now have a shared left-hand data
selector and right-hand plotting options. They read processed Wafer CSVs directly;
Merge BIN is an optional Excel export, not a prerequisite for plotting. See
[the direct plotting guide](docs/direct_plotting.md) for selection, cache behavior,
reference groups, and output locations.

## Data Policy

Real company FT data must not be committed to this repository.

Raw data, production data, generated reports, generated maps, and local experiment outputs should be stored outside the source-code repository.

## Version Baseline

v1.0.0 is the first Git-managed stable baseline of ATLAS.
