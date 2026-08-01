# AGENTS.md

## Cursor Cloud specific instructions

Penta5 is a single Node.js + Python project (a Chinese-language lottery draw
tracker / prediction dashboard / auto-betting system). There is **no build
step, no test framework, and no configured linter** — JS runs directly under
Node and Python runs directly. Run everything from the repo root (`/workspace`),
not the hardcoded `/root/penta5-runner` paths that appear in
`install_backend.sh`, the `ecosystem.*.config.js` files, and `scripts/`.

The only dependency tree is `api/` (`mysql2`); the update script runs
`npm install --prefix api`. Root JS files use only Node stdlib, and the core
Python (`algorithms/`, `remote_state_proxy.py`) uses only the stdlib. `pymysql`
(algorithm `--rds` path) and `selenium` (betting robots) are NOT installed and
are only needed for the optional RDS/robot flows.

### Services

| Service | Command (from repo root) | Port | Notes |
|---|---|---|---|
| source-proxy (required for live data) | `LOCAL_PROXY_HOST=127.0.0.1 LOCAL_PROXY_PORT=3000 REMOTE_PROXY_CACHE_FILE=/workspace/data/proxy_latest_cache.json python3 remote_state_proxy.py` | 3000 | Fetches live official draws from `uk-wl.net` (outbound internet works in this env). Override `REMOTE_PROXY_CACHE_FILE` so it does not write to `/root/...`. |
| runner (core web dashboard) | `PORT=3001 SOURCE_API_URL=http://127.0.0.1:3000/latest PENTA5_HISTORY_FROM_RDS=0 PENTA5_RDS_PRIMARY_WRITE=0 DATA_DIR=/workspace/data node --max-old-space-size=2048 penta5_backend_runner.js` | 3001 | Dashboard at `/panel`, health at `/health`. |
| rds-api (optional) | `cd api && node server.js` | 8787 | **Requires `RDS_*` secrets** (Aliyun MySQL). `api/server.js` exits immediately at startup if `RDS_HOST/USER/PASSWORD/DATABASE` are missing. |
| supervisor / betting robots | see `ecosystem.supervisor.config.js`, `robot_runtime/` | — | Optional. Robots need `selenium` + Chrome + an external betting-site login; out of scope for local dev. |

### Non-obvious caveats

- **No login needed locally.** With `PANEL_USER`/`PANEL_PASSWORD` unset the runner
  grants `admin` access, so `http://127.0.0.1:3001/panel` opens directly without
  auth. Set those env vars only if you want to test the login flow.
- **Run without a database.** Set `PENTA5_HISTORY_FROM_RDS=0` and
  `PENTA5_RDS_PRIMARY_WRITE=0` so the runner never tries the Aliyun RDS (whose
  creds are secrets); it degrades gracefully and still syncs live draws + builds
  predictions from the source-proxy alone.
- **Always set `DATA_DIR=/workspace/data`** for the runner (and
  `REMOTE_PROXY_CACHE_FILE` for the proxy). Defaults write runtime state under
  the repo / `/root/penta5-runner`.
- Draws happen every 5 minutes, so the latest period advances between requests —
  that is expected, not a bug.

### Lint / test / build

- **Lint:** no linter configured. Ad-hoc checks: `node --check <file.js>` and
  `python3 -m py_compile <file.py>`. `api/check_panel_syntax.sh` validates inline
  `<script>` blocks in `panel.html`.
- **Test:** no unit tests. `scripts/smoke_check.sh` is an integration smoke check
  of the running services (but hardcodes `/root/penta5-runner` paths).
- **Build:** none. Static HTML (`panel.html`) is served as-is.

### Quick algorithm run (fully offline, no services/secrets)

```bash
cp algorithms/data.csv.sample algorithms/data.csv
python3 algorithms/run.py --csv algorithms/data.csv   # prints exclusion advice, writes data/stable_math_filter.json
```
