"use strict";
require("../_four_ding_shape_run").runOnce(
  "four-ding-shape-parity",
  "shapeParity",
  "四定杀码·单双形态"
).catch((e) => { console.error(e); process.exit(1); });
