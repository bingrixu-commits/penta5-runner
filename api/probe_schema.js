"use strict";
const { createPool } = require("./db");
(async () => {
  const p = createPool();
  try {
    const [tables] = await p.query("SHOW TABLES");
    const names = tables.map((r) => Object.values(r)[0]);
    const out = {};
    for (const name of names) {
      const [cols] = await p.query("SHOW COLUMNS FROM `" + name + "`");
      const [cnt] = await p.query("SELECT COUNT(*) AS c FROM `" + name + "`");
      out[name] = {
        count: Number(cnt[0].c),
        columns: cols.map((c) => c.Field),
      };
    }
    console.log(JSON.stringify(out, null, 2));
  } finally {
    await p.end();
  }
})().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
