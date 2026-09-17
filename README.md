# ATLAS

**Automated Test Learning & Analytics System**

ATLAS is an internal semiconductor FT/Wafer data analysis toolkit.

## Version

Current version:

v1.10.0

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
- Recursive ZIP/GZ preview and flat Lot-level CSV preparation with recoverable backups
- Direct cleaned-CSV plotting with Product/Lot/Wafer selection and shared bounded cache
- Named analysis tasks, replaceable previews, explicit high-resolution versions and history restoration
- Multi-select probability X-axis ranges and cached full-population distribution calculations
- Verified input-rename migration, selected-Wafer replacement confirmation, audit and old-cleaned-version backups

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

Use the **数据清洗与良率汇总** tab for root-folder processing. Place vendor ZIP/GZ
packages anywhere under Product/Lot, then click **预览压缩文件 / 待整理文件**.
Review the file list, then **确认解压并整理 CSV**. It extracts all
CSV members without filtering by naming Regex. Identity checks happen in the next
incremental step; unmatched files require confirmation, not silent exclusion.
CSV files keep vendor filenames and are placed directly under Lot. Archives, other
formats and nested originals are moved out of Lot into the product's
`.atlas/ingest_backups/<run_id>/` (recoverable, not permanently deleted).
Generated output directories are protected. Then scan identities and execute yield
processing in the separate second button group. Cleaned files are named
`Product_Lot_W01_summary_cleaning.csv`. The four legacy tabs are commented out in
main; their modules remain available as standalone tools. See
[the incremental pipeline guide](docs/incremental_pipeline.md) for directory layout,
product configuration, die-count checks, recovery, and CLI usage.

Unchanged-content input renames are matched only within the same Wafer, by unique
content fingerprints and test-run identity, then migrated on execution. If a
renamed/corrected dataset cannot be proved equivalent or files are intentionally
removed, select the registered Wafer rows and use **预览 / 确认替换所选片输入**.
Review old/new paths and SHA256, enter a reason, explicitly confirm the complete
input set, and recalculate only those Wafers. Approval expires if data/config/state
changes. Prior registered metrics and cleaned CSVs are preserved under
`Product/.atlas/input_history/<run_id>/<Lot>/Wxx_<key>/`; overwritten/deleted raw
files cannot be recovered by this feature. Original archive provenance is retained.

The BIN Map, Test Map, and Probability tabs now have a shared left-hand data
selector and right-hand plotting options. They read processed Wafer CSVs directly;
Merge BIN is no longer shown in main and is not a prerequisite for plotting.
Create/select a named analysis task, adjust Lots and settings, then **更新预览（不归档）**.
The current draft uses all valid data at 90 DPI; **保存高清版本** lets you select
a parent directory (default `plots/<kind>`) and name a folder for 300 DPI images.
Task history indexes these locations without keeping another permanent image copy.
History restores selection and options
without overwriting saved images. Probability supports simultaneous **自动规格**
and **锁定规格** output, sharing the cached sorted full-population curves. See
[the direct plotting guide](docs/direct_plotting.md) for selection, cache behavior,
reference groups, and output locations.

## Data Policy

Real company FT data must not be committed to this repository.

Raw data, production data, generated reports, generated maps, and local experiment outputs should be stored outside the source-code repository.

## Version Baseline

v1.0.0 is the first Git-managed stable baseline of ATLAS.
