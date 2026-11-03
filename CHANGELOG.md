# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/). The version lives in
`recon_agent/__init__.py`; release by tagging the commit `vX.Y.Z` (see docs/DEPLOYMENT.md).

## [Unreleased]

## [0.2.0] - 2026-11-03

Deployment readiness: the worklist can be shared with several people on a server.

### Added
- Username and password sign-in for the worklist and the source simulator. Accounts live in
  `output/users.json` (PBKDF2 hashes) with the role set per account. `python -m recon_agent users`
  lists, seeds, adds, removes accounts and changes passwords and roles.
- Demo accounts (`planner1`, `planner2`, `supervisor1`, `supervisor2`, `manager1`), created on first
  start with random passwords printed to the log once, or `RECON_DEMO_PASSWORD` for all of them.
- Signed, expiring session cookies (`RECON_SECRET_KEY`); a password change or removed account ends
  that person's sessions, a role change applies at once.
- Failed sign-in throttling (5 per username, 50 per network address, per 15 minutes).
- `/healthz` on both apps; `--version`; the version in the API and the image labels.
- `docker-compose.yml` with optional TLS (Caddy) and simulator profiles, read-only containers.
- GitHub Actions: tests and image build on every push and pull request; on a `vX.Y.Z` tag, the image
  is published to GitHub Container Registry with a GitHub release.
- `pyproject.toml`, `requirements-dev.txt`, `docs/DEPLOYMENT.md`.

### Changed
- The sign-in screen asks for a username and password; the role is no longer chosen there.
- `/api/docs` is off unless `RECON_API_DOCS=on`.
- Dependencies are pinned.
- The assistant form no longer uses an inline `hx-on` handler (the page runs without `eval`).

### Removed
- `RECON_SIM_PASSCODE`: simulator operators sign in with accounts.
- Trust in `X-Remote-User` / `X-Remote-Role` request headers, which any client could send.

### Security
- Session cookies can no longer be edited to become another person or role.
- Error fragments escape their text (a crafted flag ID could inject HTML).
- Sign-in redirects stay on the site (`/\host` and control characters are refused).
- Security headers on every response: Content-Security-Policy without `unsafe-eval`, frame
  denial, `nosniff`, referrer policy, HSTS over HTTPS, `no-store` on pages.
- Cross-site POST/PUT/PATCH/DELETE requests are refused.
- The corrections token is compared in constant time.
- Planner decisions and corrections are written atomically, so a crash cannot corrupt them.

## [0.1.0] - 2026-10-29

First version in the repository: reconciliation engine (R01-R19), exception worklist, simulated
event feed and source simulator, write-back POC, OCI Generative AI narration and assistant.
