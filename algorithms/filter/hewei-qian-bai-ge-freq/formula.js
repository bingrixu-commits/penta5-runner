"use strict";

const kit = require("../../_shared/trio_hewei_kit");

const DEF = kit.TRIO_DEFS.wanQianGe;
const KEY = "wanQianGeFreq";

function calculate(history, performance) {
  return kit.calculateFreqHeweiKill(
    history,
    kit.trioActualGetter(DEF.indexes),
    {
      label: DEF.label + "(频次)",
      key: KEY,
      performanceKey: `hewei.${KEY}`,
      performance
    }
  );
}

module.exports = {
  id: "hewei-qian-bai-ge-freq",
  label: "千百个合尾·频次",
  def: DEF,
  resultKey: KEY,
  calculate
};
