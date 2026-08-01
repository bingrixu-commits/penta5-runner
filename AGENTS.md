# AGENTS.md

## Cursor Cloud specific instructions

Penta5 Runner is a single lottery-analysis product deployed as several cooperating
processes. For local dev the four core pieces are: a local **MariaDB** (substitute for
the production Alibaba RDS), a Python **source-proxy** (port 3000), a Node **rds-api**
(port 8787), and the Node **runner** (port 3001, serves the panel + `/state`). PM2 and
the `ecosystem.*.config.js` files are production-only (they hardcode `cwd:/root/penta5-runner`);
for dev, run the processes directly as shown below.

Standard commands live in `install_backend.sh`, `api/README.md`, and `api/package.json`
(`npm start`, `npm run init-schema`). Only the non-obvious dev caveats are captured here.

### Environment / startup caveats (not handled by the update script)

- **`.env` at repo root is required and is gitignored**, so it lives only in the VM
  snapshot (never committed). It points `RDS_*` at the local MariaDB and sets the panel
  password. If it goes missing, recreate it: `RDS_HOST=127.0.0.1`, `RDS_PORT=3306`,
  `RDS_USER=penta5`, `RDS_PASSWORD=penta5pass`, `RDS_DATABASE=penta5`, `PANEL_USER=admin`,
  `PANEL_PASSWORD=penta5dev`. Env is read once at process start (no hot reload) — restart
  the runner/rds-api after editing `.env`.

- **MariaDB is not managed by systemd here.** Start it manually and note it needs its
  runtime socket dir first (cleared on reboot):
  `sudo mkdir -p /run/mysqld && sudo chown mysql:mysql /run/mysqld` then
  `sudo mariadbd --user=mysql &`. DB/user already exist in the snapshot; if recreating:
  `CREATE DATABASE penta5; CREATE USER 'penta5'@'127.0.0.1' IDENTIFIED BY 'penta5pass'; GRANT ALL ON penta5.* TO 'penta5'@'127.0.0.1';`

- **Schema init gotcha:** `node api/init_schema.js --force` splits `schema.sql` on `;\n`
  and drops any chunk starting with `--`. Because `official_draws` is preceded by a
  comment block, that table is **skipped** on a fresh empty DB while the other 5 tables
  are created. Create `official_draws` manually from the DDL in `api/schema.sql` if it is
  missing (prod RDS already has it, so this only bites empty dev DBs).

### Start the services (dev)

Run each in its own long-lived shell/tmux session, in this order:

- source-proxy: `python3 remote_state_proxy.py`  (fetches real draws from `uk-wl.net`; egress works)
- rds-api:      `cd api && node server.js`
- runner:       `node penta5_backend_runner.js`  (or `node start_penta5_compat.js` on older Node)

Health: `curl 127.0.0.1:3000/health`, `curl 127.0.0.1:8787/health`, `curl 127.0.0.1:3001/health`.
The runner auto-pulls new draws every ~60s and on startup; `POST /sync` forces one.

### Panel auth (non-obvious)

- Panel: `http://<host>:3001/panel`. The browser **login form is password-only and only
  ever grants the `viewer` role** (read-only: GET `/state`, `/history`, `/panel`,
  `/stable-math-filter`). With no `PANEL_VIEWER_PASSWORD` set, submit `PANEL_PASSWORD`.
- **Admin** (write endpoints such as `POST /sync`) is available only via **HTTP Basic Auth**
  using `PANEL_USER` + `PANEL_PASSWORD` (e.g. `curl -u admin:penta5dev -X POST .../sync`).

### Lint / test / build

There is no lint step, no formal test suite, and no build step (panel/enhanced HTML are
served as-is). The only smoke check is `scripts/smoke_check.sh`, but it targets PM2 and
the `/root/penta5-runner` paths, so it is not directly usable in this dev layout — verify
via the three `/health` endpoints and the panel instead.
