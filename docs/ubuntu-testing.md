# Ubuntu verification guide

This project is developed from Windows at times, but Windows command results are not treated as authoritative for verification. Use this document when Codex asks for test feedback: run the commands on the Ubuntu working environment, then paste the exact output back into the thread.

## Environment policy

- Authoritative environment: Ubuntu.
- Non-authoritative environment: Windows / PowerShell on the desktop machine.
- Codex may inspect files on Windows and make edits, but should not mark test status as passed or failed from Windows-only results.
- If Windows and Ubuntu results disagree, Ubuntu wins.

## One-time setup

From the repository root:

```bash
python3 --version
node --version
npm --version
```

Expected baseline:

- Python: 3.12 or newer.
- Node: compatible with the version locked by `web/package-lock.json`.
- npm: available on `PATH`.

Install dependencies if needed:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cd web
npm ci
cd ..
```

If the project uses `uv` on Ubuntu, this is also acceptable:

```bash
uv sync --extra dev
cd web
npm ci
cd ..
```

## Standard backend checks

Run from the repository root:

```bash
source .venv/bin/activate
python -m pytest tests/wf_engine -q
```

For a more diagnostic run:

```bash
source .venv/bin/activate
python -m pytest tests/wf_engine -vv --maxfail=1
```

## Standard frontend checks

Run from `web/`:

```bash
npm run build
npm run lint
```

If `npm run lint` reports React hook compiler/lint errors, paste the complete error block. Do not summarize only the first line.

## Full verification bundle

Run these in order and report each command's exit code:

```bash
source .venv/bin/activate
python -m pytest tests/wf_engine -q
cd web
npm run build
npm run lint
```

## Feedback format for Codex

Paste results in this shape:

```text
Ubuntu verification

commit: <git commit hash tested>

backend:
command: python -m pytest tests/wf_engine -q
exit code: <0/nonzero>
output:
<paste output>

frontend build:
command: npm run build
exit code: <0/nonzero>
output:
<paste output>

frontend lint:
command: npm run lint
exit code: <0/nonzero>
output:
<paste output>
```

## What Codex should do with results

- Treat Ubuntu failures as real until investigated.
- Do not rely on Windows/PowerShell test output to override Ubuntu results.
- If a failure is environment-specific, document the environment difference before changing code.
- Keep commits small enough that a failing Ubuntu result can be traced to one logical change.
