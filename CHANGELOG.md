# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added / Changed
- CI workflow running pytest on pushes to main and pull requests (Python 3.10 and 3.12)
- Dependabot config for pip and GitHub Actions (weekly)
- requirements.lock (uv pip compile, Python 3.10 target) for reproducible installs
- .env.example listing the STEWARD_* variables the code reads; .env added to .gitignore
- SECURITY.md with private reporting contact
