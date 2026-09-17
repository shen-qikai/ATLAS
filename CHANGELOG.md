# Changelog

All notable changes to ATLAS will be documented in this file.

The version format follows:

MAJOR.MINOR.PATCH

## [v1.10.0] - 2026-09-17

### Added

- High-resolution save dialogs for BIN Map, Test Map and Probability: select a
  parent directory and enter a memorable folder name, defaulting to `plots/<kind>`.
- Task history indexes custom save locations, including folders outside the
  product directory; old `tasks/.../v001` archives remain readable. New GUI saves
  place images directly in the chosen folder, with no extra permanent task copy.
- Identical saves reuse their folder; saving identical plots to another location
  copies the existing high-resolution output without rendering again.

### Changed

- BIN Map product/Lot/Wafer labels use an above-wafer title, wrapping long labels
  instead of covering the wafer. Existing BIN colors and calculations are unchanged.
- Cache log wording explicitly distinguishes cumulative CSV reads from in-memory
  data reuse. Draft preview paths and naming remain unchanged.
- Synthetic plotting/export tests and hidden UI checks cover named save, external
  viewing/history, cancellation, reuse, collisions and above-wafer title placement.

## [v1.9.0] - 2026-09-17

### Added

- Automatic, same-Wafer input rename matching using full SHA256 and unchanged
  initial/retest identity; requires a unique whole-input-set bijection. Execution
  recalculates that Wafer so cleaned `source_file` provenance uses current paths.
- Read-only selected-Wafer replacement preview with old/new paths, sizes, SHA256,
  input counts and run labels; explicit complete-set acknowledgement and reason.
- Content/config/previous-state/archive-bound approvals that only recalculate
  selected Wafers, expire on any relevant change, and cannot override naming errors,
  unprepared archives, CSV validation or an empty current input set.
- Atomic Wafer/file/archive/audit publication, per-change prior cleaned/metric
  backups under `.atlas/input_history/`, and rollback of newly published cleaned
  output if audit/database publication fails.
- Synthetic coverage and hidden UI checks for migration, intentional reductions,
  approval expiry, selected-only execution, archive provenance, rollback and cancellation.

### Changed

- Registered prepared CSV corrections now update the affected Wafer rather than
  blocking unrelated data: same-content aliases retain original archive-member
  identities, corrected/removed members are retired without altering the original
  archive fingerprint or pretending corrected CSVs came from that archive.
- Replacement previews always hash selected files. Normal UI execution strictly
  rechecks changes already discovered by its scan, even with the global full-hash
  option off. Unchanged normal runs keep their existing fast path.
- Default missing-input protection and the force-mode restriction remain; no raw
  data is renamed/deleted, no company data is added to the repository.

## [v1.8.0] - 2026-09-17

### Added

- Named, product-specific analysis tasks for BIN Map, Test Map and Probability,
  with a single replaceable draft and a reusable in-app image viewer.
- Explicit 300 DPI saved versions (`v001`, `v002`, ...), immutable archives,
  identical-save reuse, a latest-version pointer, and history selection/parameter restoration.
- A bounded 64 MiB probability calculation cache keyed by test/specification,
  grouping and the complete selected Wafer fingerprint set. Overlapping Lot
  comparisons reuse unchanged curves without sampling or averaging Wafer distributions.
- Synthetic tests for draft publication/rollback, stale/changed inputs, archive
  integrity, multiple range output, calculation reuse, and hidden UI workflows.

### Changed

- Plot buttons now update 90 DPI previews without creating an archive each time.
  Explicit save re-renders at 300 DPI from the same full valid population.
- Probability X-axis modes are multi-select: automatic axis range, specification
  lock and manual range. Each produces its own images and 2×2 pages; the existing
  specification padding/fallback behavior is unchanged.
- Managed drafts and saved versions detect modified/unregistered files rather
  than silently replacing them. Changed data or parameters require a new preview.
- Existing legacy plot folders, CSV cleaning and yield report behavior are preserved.

## [v1.7.1] - 2026-09-17

### Changed

- Centered all cells in the three automated Excel reports and supporting sheets.
- Fit column widths to formatted display values across every row, using font
  measurements for Chinese/Latin text rather than a fixed 14–32 character range.
  Kept numeric precision and metrics unchanged; compacted header/default row heights.
- Removed the fixed explanation-column width. Exceptionally long content wraps
  at Excel's maximum column width with adjusted row heights.
- Documented expected-die count configuration and cache-only report regeneration.

## [v1.7.0] - 2026-09-17

### Added

- Read-only archive/cleanup preview before explicitly confirmed preparation;
  preview records nested paths and detects stale inventory/content before execution.
- Recursive ZIP/GZ discovery under Lot, flat CSV publication directly into Lot,
  and recoverable removal of original archives and other formats into the product's
  `.atlas/ingest_backups/<run_id>/`. Generated output/cache directories are protected.
- Migration of active registered legacy imports to flat layout, with historical
  import files backed up rather than reused as current inputs.
- Synthetic coverage for nested folders, flat processing/plotting, backups, stale
  preview, collisions, rollback, legacy migration and asynchronous UI steps.

### Changed

- Split CSV preparation and identity/yield processing into two labeled button groups
  and separate preview tables. Preparation requires preview and confirmation.
- Same-name identical CSVs share one flat file; differing contents block the Lot,
  rather than silently overwriting or mixing inputs.
- Registered flat CSVs remain valid after original archives move into backup.
  CLI adds `--preview-archives` and requires explicit preparation cleanup approval.

## [v1.6.0] - 2026-09-17

### Added

- Added a CSV preparation step for ZIP and single-file CSV GZ, independent of YAML
  identity rules. Original archives stay untouched; non-CSV members are not extracted.
- Added incremental archive manifests, per-bundle output isolation, safe temporary
  preparation, provenance and retry logging. Invalid/unprepared archives block their Lot.
- Shared processing/plot inventory reads registered imported CSVs, handles identical
  same-name duplicates, and detects archive/member changes. Updated archive contents
  replace complete prior bundles without discarding registered CSV members.
- Unmatched CSVs are visible as pending confirmation; explicit ignore/restore keeps
  files and stores content-specific confirmation in the product cache.

### Changed

- Commented out the four legacy tabs/imports/UI calls in main rather than deleting
  their modules. Main now has cleaning/yield summary and three direct plotting pages.
- Renamed the pipeline page to 数据清洗与良率汇总 and increased Log from 9 to 23
  text rows (approximately 2.5x), with a draggable vertical divider.
- Extended raw-data ignore rules to compressed archives and STD/STDF files.

## [v1.5.0] - 2026-09-17

### Changed

- The automated workflow reads vendor filenames directly without renaming raw CSVs;
  manual normalization is clearly marked as optional in the UI.
- Unified cleaned output names as `Product_Lot_W01_summary_cleaning.csv`. Existing
  demo outputs with different names are recomputed on the next incremental run,
  rather than permanently retaining legacy output names.
- After successful registration, unchanged registered old outputs are archived under
  `.atlas/retired_cleaned/`; modified old files and unregistered target files are not
  overwritten. Preview remains read-only, and plot selection detects pending naming updates.
- Updated optional legacy report/export readers for the W01 naming format and added
  synthetic naming, provenance, migration and collision tests.

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
