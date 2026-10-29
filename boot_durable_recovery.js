"use strict";

/** A startup-only gate: restore into a clone before allowing any state writes. */
function createDurableBootRecovery(options) {
  const opts = options || {};
  const clone = opts.clone || ((value) => JSON.parse(JSON.stringify(value)));
  const sleep = opts.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const retryMs = Math.max(2000, Number(opts.retryMs || 5000));
  let ready = opts.required !== true;
  let inFlight = null;
  let completed = ready ? { ok: true, skipped: true, reason: "local-store" } : null;

  const notify = (status) => {
    try { if (typeof opts.onStatus === "function") opts.onStatus(status); } catch (_) {}
  };

  function recover() {
    if (ready) return Promise.resolve(completed);
    if (inFlight) return inFlight;
    inFlight = (async () => {
      // Freeze the existing local account once. Failed fetches and status reads
      // must not gradually replace it with a half-loaded or newly empty account.
      let original = null;
      let attempts = 0;
      while (!ready) {
        attempts += 1;
        notify({ status: "waiting-for-durable-state", attempts });
        try {
          if (original === null) original = clone(opts.readLocal());
          const candidate = clone(original);
          const outcome = await opts.hydrate(candidate);
          if (outcome && outcome.ok === true) {
            const localTip = original.currentPrediction;
            const restoredTip = candidate.currentPrediction;
            const identity = (tip) => String(tip && (tip.basePeriod || tip.baseUid) || "");
            const localPeriod = identity(localTip), restoredPeriod = identity(restoredTip);
            // Keep a sealed, equally recent or newer local tip. Its original
            // pre-draw publication must not be replaced by a lagging DB mirror.
            if (localTip && localTip.allSnapshotsSealed === true && /^\d{11}$/.test(localPeriod)
                && (!/^\d{11}$/.test(restoredPeriod) || localPeriod >= restoredPeriod)) {
              candidate.currentPrediction = clone(localTip);
            }
            opts.commit(candidate);
            completed = Object.assign({}, outcome, { bootAttempts: attempts });
            ready = true;
            notify({ status: "ready", attempts });
            return completed;
          }
        } catch (_) {
          // The public state contains a fixed category, never provider errors.
        }
        notify({ status: "waiting-for-durable-state", attempts });
        await sleep(retryMs);
      }
      return completed;
    })();
    return inFlight;
  }

  return { isReady: () => ready, recover };
}

module.exports = { createDurableBootRecovery };
