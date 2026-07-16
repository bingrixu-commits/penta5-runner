"use strict";
const { createPool } = require("./db");
(async () => {
  const p = createPool();
  try {
    const [t] = await p.query("SHOW TABLES");
    const tables = t.map((r) => Object.values(r)[0]);
    const out = { tables: {} };
    for (const name of tables) {
      const [c] = await p.query("SELECT COUNT(*) AS c FROM `" + name + "`");
      out.tables[name] = Number(c[0].c);
    }
    const [latest] = await p.query(
      "SELECT period, uid, open_time FROM official_draws ORDER BY open_time DESC, id DESC LIMIT 1"
    );
    out.latest = latest[0] || null;
    const [snap] = await p.query(
      "SELECT id, snapshot_name, import_status, history_count, prediction_count FROM system_snapshots ORDER BY id DESC LIMIT 3"
    );
    out.snapshots = snap;
    console.log(JSON.stringify(out, null, 2));
  } finally {
    await p.end();
  }
})().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
