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
- Hosted demo: Dockerfile (python:3.12-slim), scripts/start.sh (MCP server + web app in one container), railway.json, README "Deploy" section, DEPLOY_AWS.md (EC2 t4g.micro + Bedrock Nova Micro via IAM role)
- GET /health cheap liveness endpoint on the web app; GET /health on the MCP server
- Demo mode (STEWARD_DEMO_MODE=1): per-browser session cookie, per-session agent, per-session SQLite store on the MCP server (X-Steward-Session header), periodic reset (STEWARD_DEMO_RESET_HOURS), session cap, idle timeout, message length cap; default local behaviour unchanged
