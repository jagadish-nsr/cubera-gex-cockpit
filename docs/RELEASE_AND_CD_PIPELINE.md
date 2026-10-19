# Cubera GEX Cockpit — Release & CD Pipeline Architecture Plan

> **Objective**: Enable continuous development, live hot-reloading, and aggressive feature experimentation on a dedicated **Development Environment** (Port `8650`), while maintaining an ultra-stable, uninterrupted **Production Cockpit** (Port `8640`) for live market trading, backed by a 1-click **Promotion & Rollback Pipeline**.

---

## 1. Executive Summary & Topology

In a financial trading cockpit where intraday decisions depend on live option chain data, order flow CVD, and GEX levels, **editing code in place on a live production server is high risk**:
- A temporary syntax error or unhandled exception immediately kills the live terminal during active market hours (09:15–15:40 IST).
- Auto-reloading restarts WebSocket feeds, interrupting active order flow monitoring and session telemetry.
- Git branch checkouts (`git checkout feature-x`) instantly alter files under the running Python server on disk.

### The Solution: Dual-Environment Git Worktree Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │            Git Repository Core               │
                    │   (git@github.com:jagadish-nsr/cubera...)   │
                    └──────────────────────┬───────────────────────┘
                                           │
                 ┌─────────────────────────┴─────────────────────────┐
                 │                                                   │
                 ▼                                                   ▼
┌─────────────────────────────────┐                 ┌─────────────────────────────────┐
│     DEVELOPMENT WORKTREE        │                 │      PRODUCTION WORKTREE        │
│  Path: ~/cubera-gex-cockpit     │                 │  Path: ~/cubera-gex-cockpit-prod│
│  Branch: `dev` / `feature/*`    │                 │  Branch: `main` (Pinned Tag)    │
│  Port: 8650                     │                 │  Port: 8640                     │
│  Mode: --reload (Hot-Reload)    │                 │  Mode: Production Daemon        │
│  UI: Amber [DEV MODE] Watermark │                 │  UI: Clean Institutional Header │
└────────────────┬────────────────┘                 └────────────────▲────────────────┘
                 │                                                   │
                 │              1-CLICK PROMOTION PIPELINE           │
                 └──────►  ./scripts/promote.sh  ────────────────────┘
                              1. Run Pytest Suite
                              2. Merge dev -> main & Tag
                              3. Pull in Prod Worktree
                              4. Graceful SIGHUP/Restart
                              5. Smoke Health Check Probe
                              6. Auto-Rollback on Failure
```

---

## 2. Environment Specifications

| Dimension | Production Environment (`PROD`) | Development Environment (`DEV`) |
| :--- | :--- | :--- |
| **Workspace Directory** | `/Users/jagadish/MyProjects/Applications/cubera-gex-cockpit-prod` | `/Users/jagadish/MyProjects/Applications/cubera-gex-cockpit` |
| **Active Git Branch** | `main` (only merges via tags: `v5.4.0`) | `dev` or `feature/<feature-name>` |
| **Serving Port** | `8640` (`http://localhost:8640`) | `8650` (`http://localhost:8650`) |
| **Execution Mode** | Production daemon (no auto-reload, max stability) | `uvicorn --reload --port 8650` |
| **Process Manager** | macOS `launchd` / background runner (`run_prod.sh`) | Foreground interactive (`run_dev.sh`) |
| **Config File** | `.env.production` (or `.env`) | `.env.development` |
| **Telemetry / UI Badge** | Green / Minimal Institutional Status | Amber / High-Visibility `[DEV MODE :8650]` Badge |
| **Data Writers** | Authoritative writer to `data/gex.db`, `data/sentinel.db` | Reads shared DB; writes to test sandbox or dry-run |
| **Dhan API & NSE** | Real live credentials & rate gate | Shared read tokens / Simulator mode enabled |

---

## 3. Git Worktree Pattern (Zero Duplication, True Isolation)

Instead of cloning a separate repository and dealing with out-of-sync remotes, we use **Git Worktrees**:

### Why Git Worktree?
1. Both environments share the exact same `.git` database and commit history (no extra disk bloat).
2. You can have `cubera-gex-cockpit` on branch `dev` and `cubera-gex-cockpit-prod` on branch `main` checked out **simultaneously**.
3. Editing files in `dev` never touches the files served by `prod`.

### One-Time Setup Command
```bash
# In your current project directory:
cd /Users/jagadish/MyProjects/Applications/cubera-gex-cockpit

# Ensure git status is clean and create dev branch
git checkout -b dev

# Add the production worktree alongside current repo
git worktree add ../cubera-gex-cockpit-prod main
```

Now you have:
- `~/MyProjects/Applications/cubera-gex-cockpit` (Dev, branch `dev`)
- `~/MyProjects/Applications/cubera-gex-cockpit-prod` (Prod, branch `main`)

---

## 4. The 5-Stage Automated Promotion Pipeline

When you finish developing and testing on Dev (`localhost:8650`), you trigger promotion:

```bash
./scripts/promote.sh [patch|minor|major]
# or: ./scripts/promote.sh v5.4.0
```

### Stage 1: Pre-Flight Quality Gates
The promotion script enforces strict checks before allowing a release:
1. **Working Tree Cleanliness**: Ensures no uncommitted or untracked breaking files exist in Dev.
2. **Automated Test Suite Execution**:
   - Runs `python3 -m pytest tests/test_web_server_endpoints.py` (API contract validation).
   - Runs `python3 -m pytest tests/test_cockpit_market_hours.py tests/test_dhan_adapter.py`.
   - Runs full test suite (or fast critical path). If any test fails, promotion **halts immediately**.
3. **Syntax & Linter Verification**: Validates AST integrity across Python and JavaScript files.

### Stage 2: Git Synchronization & Semantic Tagging
1. Switches to `cubera-gex-cockpit`, pushes `dev` to `origin/dev`.
2. Switches to `main`, merges `dev` (fast-forward or clean merge commit).
3. Computes semantic release tag (e.g. `v5.4.0`) based on git tags or argument.
4. Creates annotated git tag: `git tag -a v5.4.0 -m "Release v5.4.0: [commit messages]"`.
5. Pushes `main` and the new tag to GitHub.

### Stage 3: Production Worktree Deployment
1. Transitions to `/Users/jagadish/MyProjects/Applications/cubera-gex-cockpit-prod`.
2. Pulls new `main` / checks out the release tag: `git checkout v5.4.0`.
3. Verifies and installs new dependencies if `requirements.txt` changed:
   `pip install -r requirements.txt --quiet`.

### Stage 4: Graceful Server Reload & Zero-Downtime Swap
1. **Database Protection (Default)**:
   - Persistent runtime databases (`data/sentinel.db`, `data/journal/market_snapshot.db`) are **never** blindly overwritten from Dev.
   - This protects genuine live market data accumulated in Production during trading hours.
   - If a deliberate database overwrite is required (e.g. repairing a damaged Prod DB from clean Dev), supply the opt-in flag:
     ```bash
     ./scripts/promote.sh --sync-db
     ```
   - For database cleansing or backfilling without code deployment:
     ```bash
     python3 scripts/db_cleanse_and_sync.py --target prod
     ```
2. Finds the running production PID on port 8640.
3. Initiates graceful reload:
   - Uses PID file `logs/prod_server.pid`.
   - Spawns new process, binds port 8640, and terminates old process cleanly via `kill -15`.
4. Production server boots up cleanly with the newly promoted code.

### Stage 5: Post-Deployment Smoke Health Check & Rollback
1. Pings the production health endpoint:
   ```bash
   curl -s -f http://localhost:8640/api/health
   ```
2. Validates that the returned status is `"ok"` and the version matches `v5.4.0`.
3. Validates critical endpoints (`/api/cockpit-data`, `/api/sentinel-tape`).
4. **Automated Rollback Trigger**:
   - If `/api/health` does not respond within 10 seconds or returns an HTTP error, the script automatically triggers `./scripts/rollback.sh`, rolling back `cubera-gex-cockpit-prod` to the previous tag and restarting.

---

## 5. Instant 1-Click Rollback Mechanism

If you notice an unexpected quant anomaly, visual bug, or data issue in production after deployment:

```bash
./scripts/rollback.sh
```

**What it does:**
1. Checks the deployment history log (`logs/releases.log`).
2. Identifies the previous stable tag (e.g., `v5.3.0`).
3. Checks out `v5.3.0` in the `cubera-gex-cockpit-prod` directory.
4. Restarts the production server on port 8640.
5. Verifies `/api/health`.
6. Logs the rollback event with timestamp and rationale.

---

## 6. Continuous Integration (CI) on GitHub Actions

For team collaboration or cloud backup safety, every push to GitHub triggers CI:

### `.github/workflows/ci.yml`
- **Trigger**: Every push or Pull Request to `dev` or `main`.
- **Jobs**:
  1. **Lint & Formatting**: `ruff` / `flake8` check.
  2. **Core Quant & Engine Tests**: `pytest` on `gex/`, `regime/`, `target_engine/`.
  3. **FastAPI Web Cockpit Integration**: `pytest tests/test_web_server_endpoints.py`.
  4. **IST Timezone & Contract Audits**: `pytest tests/test_universal_ist_timestamps.py`.

---

## 7. macOS Production Process Resilience (`launchd`)

To ensure the Production Cockpit on port 8640 is always up, even if the terminal closes or the Mac restarts:

Create `~/Library/LaunchAgents/com.cubera.gexcockpit.plist`:
- Configured with `KeepAlive: true` and `RunAtLoad: true`.
- Automatically restarts if the process terminates unexpectedly.
- Logs stdout/stderr to `/Users/jagadish/MyProjects/Applications/cubera-gex-cockpit-prod/logs/prod.log`.

---

## 8. Summary of Release Commands

| Action | Command | Purpose |
| :--- | :--- | :--- |
| **Start Dev Server** | `./run_dev.sh` | Runs on `http://localhost:8650` with hot-reload enabled. |
| **Start Prod Server** | `./run_prod.sh` | Runs on `http://localhost:8640` in production background mode. |
| **Run Pre-Flight Tests** | `make test` or `pytest -q` | Validates all 20+ endpoint tests and quant models. |
| **Promote Dev to Prod** | `./scripts/promote.sh [tag]` | Tests, merges, tags, deploys to prod worktree, and reloads. |
| **Rollback Prod** | `./scripts/rollback.sh` | Instantly reverts prod to previous release tag. |
| **Check Health** | `curl http://localhost:8640/api/health` | Confirms version, uptime, and DB health. |
