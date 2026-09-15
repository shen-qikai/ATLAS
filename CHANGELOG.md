# Changelog

All notable changes to ATLAS will be documented in this file.

The version format follows:

MAJOR.MINOR.PATCH

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
