"use strict";

/**
 * 仅用于空库初始化。线上已是 existing-v71 时不要用本脚本「重建」表。
 * schema.sql 已与 dump_ddl.js 对齐；CREATE TABLE IF NOT EXISTS 不会改已有列。
 */
const fs = require("fs");
const path = require("path");
const { createPool } = require("./db");

async function main() {
  const force = process.argv.includes("--force");
  const schemaPath = path.join(__dirname, "schema.sql");
  const sql = fs.readFileSync(schemaPath, "utf8");
  const statements = sql
    .split(/;\s*\n/)
    .map((s) => s.trim())
    .filter((s) => s && !s.startsWith("--"));

  const pool = createPool();
  const conn = await pool.getConnection();
  try {
    const [existing] = await conn.query("SHOW TABLES");
    const tables = existing.map((row) => Object.values(row)[0]);
    if (tables.length && !force) {
      console.log(
        JSON.stringify(
          {
            ok: true,
            skipped: true,
            reason: "tables-already-exist",
            hint: "线上库请用 dump_ddl.js / probe_schema.js 核对；空库初始化可加 --force",
            tables,
          },
          null,
          2
        )
      );
      return;
    }
    for (const statement of statements) {
      await conn.query(statement);
    }
    const [after] = await conn.query("SHOW TABLES");
    console.log(
      JSON.stringify(
        {
          ok: true,
          tables: after.map((row) => Object.values(row)[0]),
        },
        null,
        2
      )
    );
  } finally {
    conn.release();
    await pool.end();
  }
}

main().catch((err) => {
  console.error(String(err && err.message ? err.message : err));
  process.exit(1);
});
