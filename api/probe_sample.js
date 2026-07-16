"use strict";
const { createPool } = require("./db");
(async () => {
  const p = createPool();
  try {
    const [latest] = await p.query(
      "SELECT period, uid, open_time, ball1, ball2, ball3, ball4, ball5 FROM official_draws ORDER BY open_time DESC, id DESC LIMIT 3"
    );
    const [snaps] = await p.query(
      "SELECT id, snapshot_sha256, source_path, state_version, history_count, prediction_count, created_at FROM system_snapshots ORDER BY id DESC LIMIT 5"
    );
    const [pred] = await p.query(
      "SELECT prediction_id, base_period, model_version, evaluated, generated_at FROM model_predictions ORDER BY id DESC LIMIT 3"
    );
    const [states] = await p.query("SELECT state_key, state_type, updated_at FROM model_states");
    const [counters] = await p.query(
      "SELECT counter_key, counter_type, counter_day, total_count, match_count FROM model_counters LIMIT 20"
    );
    console.log(
      JSON.stringify({ latest, snaps, pred, states, counters }, null, 2)
    );
  } finally {
    await p.end();
  }
})().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
