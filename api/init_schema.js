"use strict";

/**
 * 仅用于空库初始化。线上已是 existing-v71 时不要用本脚本「重建」表。
 * schema.sql 已与 dump_ddl.js 对齐；CREATE TABLE IF NOT EXISTS 不会改已有列。
 *
 * 注意：按 `;\n` 切分后，段首常有 `--` 注释块。旧逻辑会丢掉整段，导致
 * official_draws（文件最前一张表）在空库上被跳过。现改为剥掉行首 SQL 注释后再执行。
 */
const fs = require("fs");
const path = require("path");
const { createPool } = require("./db");

function stripLeadingSqlComments(statement) {
  const lines = String(statement || "").split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trim();
    if (!trimmed || trimmed.startsWith("--")) {
      i += 1;
      continue;
    }
    break;
  }
  return lines.slice(i).join("\n").trim();
}

function splitSchemaStatements(sql) {
  return String(sql || "")
    .split(/;\s*\n/)
    .map((chunk) => stripLeadingSqlComments(chunk.trim()))
    .filter((statement) => {
      if (!statement) return false;
      // 纯注释段已剥空；若仍以注释开头则跳过
      if (statement.startsWith("--")) return false;
      return true;
    });
}

async function main() {
  const force = process.argv.includes("--force");
  const schemaPath = path.join(__dirname, "schema.sql");
  const sql = fs.readFileSync(schemaPath, "utf8");
  const statements = splitSchemaStatements(sql);

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
    const created = [];
    for (const statement of statements) {
      await conn.query(statement);
      const match = statement.match(/CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([a-zA-Z0-9_]+)`?/i);
      if (match) created.push(match[1]);
    }
    const [after] = await conn.query("SHOW TABLES");
    const afterTables = after.map((row) => Object.values(row)[0]);
    console.log(
      JSON.stringify(
        {
          ok: true,
          statementCount: statements.length,
          createdHints: created,
          tables: afterTables,
          hasOfficialDraws: afterTables.includes("official_draws"),
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

if (require.main === module) {
  main().catch((err) => {
    console.error(String(err && err.message ? err.message : err));
    process.exit(1);
  });
}

module.exports = {
  stripLeadingSqlComments,
  splitSchemaStatements,
};
