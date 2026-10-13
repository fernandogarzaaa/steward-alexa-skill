# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] - 2026-10-13

### Fixed
- Web UI: with no model configured, every message answered "Steward is waking
  up or temporarily unavailable. Please try again in a moment." although the
  server returned a precise setup error. A 503 that carries Steward's own
  detail now shows it ("Steward is not set up yet: No model configured. Set
  STEWARD_MODEL_BASE_URL ..."), and a 502 from a failed agent run points at the
  model or the MCP server. The generic "waking up" text is kept only for
  non-JSON responses (cold start / proxy pages). Validation errors no longer
  render as `[object Object]`.

## [1.0.0] - 2026-10-13

First tagged release of the hackathon build, including the October 2026 hardening.

### Fixed (web UI)
- Non-JSON error pages (Render cold start, proxy 502) no longer show
  "Unexpected token <"; 429/413/5xx get plain-language messages.
- Pressing Enter while a reply was pending sent the message twice; input is a
  real form and sends are serialized.
- Screen readers: the conversation is an `aria-live` log, the message box has an
  accessible name, visible focus rings.
- Mobile: layout uses `100dvh` so the input bar stays above the on-screen keyboard.

### Security
- API-key auth for `/api/chat`, `/api/tools` (`STEWARD_API_KEY`) and the MCP
  Streamable HTTP endpoint (`STEWARD_MCP_API_KEY`); the agent sends the MCP key.
- Per-IP rate limits and a message-length cap on `/api/chat`; per-IP limit on `/mcp`.
- Non-loopback binds without a key are refused unless `STEWARD_ALLOW_OPEN=1`.
- Agent turns are serialized; error details are logged instead of returned.
- Public `/health` liveness endpoint; `/api/health` no longer returns the MCP URL.

### Added / Changed
- CI workflow running pytest on pushes to main and pull requests (Python 3.10 and 3.12)
- Dependabot config for pip and GitHub Actions (weekly)
- requirements.lock (uv pip compile, Python 3.10 target) for reproducible installs
- .env.example listing the STEWARD_* variables the code reads; .env added to .gitignore
- SECURITY.md with private reporting contact
