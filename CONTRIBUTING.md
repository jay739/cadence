# Contributing to Cadence

Cadence is a small, focused self-hosted service. Contributions are welcome, especially around
search-source coverage, fulfillment reliability, and the native clients planned for later
phases. This guide covers how to get a working dev setup and what the project expects from a
change.

## Development setup

### Backend (FastAPI, Python 3.11+)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example ../.env  # edit as needed; DEMO_MODE=true needs nothing else
uvicorn app.main:app --reload
```

Run the checks CI runs:

```bash
ruff check app tests
mypy app
pytest tests/unit tests/integration -q --cov=app --cov-report=term-missing
```

### Web client (React, Vite, TypeScript)

```bash
cd web
npm install
npm run dev   # proxies /api to a backend on :8000 by default
```

Checks:

```bash
npm run lint
npm run typecheck
npm run format:check
npm test
npm run build
```

### End-to-end tests

The Playwright suite boots the real backend in demo mode, serving the real built web client
(the same wiring as production), so it needs no external services or API keys.

```bash
cd web
npx playwright install --with-deps chromium
npm run test:e2e
```

## Conventional commits

Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`,
`test:`, `docs:`, `chore:`, `refactor:`, etc. This drives the semver-based release process and
the generated changelog.

## The one rule that never bends

A request in Cadence is always for a single track. If a change would make Cadence monitor,
grab, or track anything at the album or artist level, even as an opt-in convenience, it will
be rejected. That premise is the entire reason this project exists instead of using Lidarr or
Ombi.

## Pull requests

- Keep PRs focused; one logical change per PR.
- Add or update tests for behavior changes.
- Update `.env.example` if you add a new configuration variable.
- Update `docs/` or the README if user-facing behavior changes.
- CI (lint, typecheck, tests, build, dependency audit) must pass before merge.

## Security issues

Do not open a public issue for a security vulnerability. See [SECURITY.md](SECURITY.md).
