"use strict";

const kit = require("../../_shared/trio_hewei_kit");

const DEF = kit.TRIO_DEFS.wanBaiShi;
let _bound = false;

function bindRuntime(runtime) {
  if (_bound) return;
  _bound = true;
  try {
    const heweiFormula = require("../../hewei/formula");
    if (typeof heweiFormula.bindRuntime === "function") {
      heweiFormula.bindRuntime(runtime && typeof runtime === "object" ? runtime : {});
    }
  } finally {
    _bound = false;
  }
}

function calculate(history, performance, host) {
  if (host) bindRuntime(host);
  const rows = Array.isArray(history) ? history.slice(0, 300) : [];
  const heweiFormula = require("../../hewei/formula");
  return kit.calculateStableHeweiKill(
    rows,
    kit.trioActualGetter(DEF.indexes),
    {
      label: DEF.label + "(稳定)",
      key: DEF.key,
      lockKey: `v43:hewei:${DEF.key}`,
      performanceKey: `hewei.${DEF.key}`,
      performance
    },
    heweiFormula
  );
}

module.exports = {
  id: "hewei-qian-shi-ge-stable",
  label: "千十个合尾·稳定",
  def: DEF,
  bindRuntime,
  calculate
};
