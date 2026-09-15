# AGENTS.md

## Project

ATLAS - Automated Test Learning & Analytics System

## Current Baseline

Current stable baseline:

v1.0.0

## Working Rules for Codex

1. Do not change existing program behavior unless explicitly requested.
2. Before modifying code, inspect the current structure and explain the intended change.
3. Keep `main` stable.
4. Create a new branch for new features or risky changes.
5. Review the diff before committing.
6. Run available tests or basic execution checks before committing.
7. Do not commit real company FT data.
8. Do not commit CSV/XLSX raw data, generated maps, reports, logs, or local virtual environments.
9. Prefer small, clear commits with meaningful commit messages.
10. Update `CHANGELOG.md` when a user-facing feature or important behavior changes.

## Data Policy

Real company FT data must stay outside the Git repository.

Use only synthetic or anonymized sample data for examples and tests.

## Versioning

Use semantic versioning:

MAJOR.MINOR.PATCH

- MAJOR: incompatible architecture or workflow changes
- MINOR: new features added in a compatible way
- PATCH: bug fixes or small improvements
