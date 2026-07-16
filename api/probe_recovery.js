"use strict";
const { createPool } = require("./db");
(async () => {
  const p = createPool();
  try {
    const [s] = await p.query(
      "SELECT id, LEFT(snapshot_sha256,12) AS h, source_path, history_count, prediction_count, created_at FROM system_snapshots ORDER BY id"
    );
    const [[d]] = await p.query("SELECT COUNT(*) AS draws FROM official_draws");
    const [[pr]] = await p.query("SELECT COUNT(*) AS preds FROM model_predictions");
    const [[ev]] = await p.query("SELECT COUNT(*) AS evals FROM model_evaluations");
    const [latest] = await p.query(
      "SELECT period FROM official_draws ORDER BY open_time DESC, id DESC LIMIT 1"
    );
    console.log(
      JSON.stringify(
        {
          snapshots: s,
          now: {
            draws: d.draws,
            preds: pr.preds,
            evals: ev.evals,
            latest: latest[0] && latest[0].period,
          },
        },
        null,
        2
      )
    );
  } finally {
    await p.end();
  }
})().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
