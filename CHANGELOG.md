# Changelog

All notable changes to ATLAS will be documented in this file.

The version format follows:

MAJOR.MINOR.PATCH

## [v1.4.0] - 2026-09-16

### Changed

- Automated parameter reports now show failed die / wafer total die, with separate
  failure and missing-value counts. Specification boundaries pass; missing values
  are not failures. Duplicate coordinates use the last retained record for this metric.
- Replaced the automated parameter-yield filename with `Product_测试项失效率.xlsx`;
  old yield reports remain untouched as historical files.
- Removed inferred CP/FT labels from reports, selection and plot labels. Filename
  stage text no longer determines physical test stage; explicit RT ordering remains.
- Reused unambiguous legacy wafer keys without reprocessing unchanged inputs;
  ambiguous legacy identities require separate directories. Legacy duplicate-die
  metrics are upgraded during normal incremental processing.

## [v1.3.1] - 2026-09-16

### Fixed

- CSV parsing now ignores trailing unnamed columns only when all metadata and
  data cells in those columns are empty (including omitted trailing fields).
- Unnamed columns containing any content report column/row positions and require
  human confirmation; interior unnamed and duplicate named columns remain rejected.
- Added synthetic regression tests; raw CSV files remain unchanged.

## [v1.3.0] - 2026-09-16

### Added

- Added shared Product/Lot/Wafer selection with processing status, die counts and yield.
- Added direct cleaned-CSV adapters for BIN Map, Test Map and Probability, eliminating
  mandatory intermediate merged Excel files from the plotting workflow.
- Added on-demand column reads and a shared 256 MiB LRU memory cache with version invalidation.
- Added Probability reference/analysis groups, optional explicit Lot pooling, and
  separate plots when selected Wafer test units or limits differ.
- Added per-run plot folders and manifests with inputs, data hashes, options and status.
- Added synthetic catalog/cache/renderer tests and a hidden full-application GUI smoke test.

### Changed

- Plot tabs now use a two-column layout; existing modes, Notch, ranges, highlighting,
  sorting and image stitching are retained. Legacy Excel classes/entry points remain available.
- Merge BIN remains available as optional Excel export, not a plotting dependency.
- Plotting rejects stale/missing inputs and rechecks versions before/after rendering.
- SQLite cache reads now use consistent read transactions during concurrent processing.
- Test Map output filenames safely escape test names without changing plotted values.
- Generated plots are excluded from incremental raw-data discovery.

### Notes

- No raw-data cleaning or yield algorithm changes; v1.2 Wafer caches can be reused.
- Plot memory cache is session-local; selected plot data and image rendering also use memory.
- Default stamp checks cannot detect same-size/same-time replacements on cache hits;
  optional full content verification checks only selected Wafer inputs/outputs.
- Original CSVs, old exports and historical images are not deleted or overwritten.

## [v1.2.0] - 2026-09-16

### Added

- Added a UI-independent incremental Wafer pipeline and root-folder GUI/CLI entry points.
- Added per-product SQLite processing state, input fingerprints, and cached counts/metrics.
- Added three product-level Excel reports with Good BIN 1 yield, valid die counts,
  per-test valid counts, unit/limit metadata, spec versions, and processing-status sheets.
- Added incremental replacement for late retests/source corrections, failure retry,
  explicit missing-input handling, force recomputation, and cache-only report rebuilding.
- Added product cleaning/column/stage/count-tolerance settings and synthetic integration tests.

### Changed

- Manual merge/cleaning now outputs only cleaned merged CSVs with metadata;
  uncleaned merge and metadata-stripped outputs are no longer generated.
- New pipeline validation blocks incomplete Wafer results and ambiguous overlapping runs.
- Product YAML loading rejects duplicate keys; preserved existing profiles under one products key.
- Standardized filename detection now accepts dotted Lot IDs.

### Notes

- Original CSVs and existing historical generated files are not renamed or deleted by the pipeline.
- Existing manual yield/BIN/map/probability algorithms remain unchanged.
- Pipeline reports handle single-sided limits and changing test columns/limits explicitly.

## [v1.1.0] - 2026-09-16

### Added

- Added YAML-based Product Profiles with versioned, named-group Regex rules.
- Added an autocomplete Product selector restricted to enabled profiles.
- Added mandatory tabular Preview, metadata validation, and collision checks.
- Added raw/archive/normalized/unknown file classification and preprocessing logs.
- Added regression tests using synthetic filenames and data only.

### Changed

- Separated reusable preprocessing logic from the Tkinter UI.
- Kept the existing `{product}_{lot}_{wafer}#_{original}` output naming format for
  compatibility with downstream Cleaning, Yield, BIN, Map, and Probability tools.
- Made ZIP/GZ extraction preserve archives by default and reject overwrites.

## [v1.0.0] - 2026-09-15

### Added

- Established the first Git-managed baseline version of ATLAS.
- Added current stable FT/Wafer analysis scripts.
- Added project documentation files.
- Added `.gitignore` to exclude raw data, generated files, virtual environments, logs, and temporary files.

### Notes

- This version freezes the current stable behavior.
- No business logic or analysis logic was changed in this baseline commit.
- Real company FT data is excluded from version control.
