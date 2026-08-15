"use strict";

/**
 * 四定服务端分批真发 HTTP：/four-throw-config|send|status|stop
 * 接 panel 约定 + four_throw_robot.py（FOUR_THROW_JOB_LOG / pages.txt / status.json）
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");

function createFourThrowHttp(options) {
  const rootDir = options.rootDir;
  const sendJson = options.sendJson;
  const readRequestBody = options.readRequestBody;
  const urlsFile = path.join(rootDir, "four_throw_urls.json");
  const jobsDir = path.join(rootDir, "data", "four-throw-jobs");
  const pythonBin = process.env.FOUR_THROW_PYTHON || "python3";
  const robotScript = path.join(rootDir, "four_throw_robot.py");

  /** @type {Map<string, { child: import('child_process').ChildProcess|null, stopRequested: boolean }>} */
  const liveJobs = new Map();

  function isoNow() {
    return new Date().toISOString();
  }

  function ensureJobsDir() {
    fs.mkdirSync(jobsDir, { recursive: true });
  }

  function defaultConfig() {
    return {
      active: "1",
      amount: "",
      password: "",
      urls: { "1": "", "2": "", "3": "" },
      labels: { "1": "格式1", "2": "格式2", "3": "格式3" },
      note: "amount 填金额；password 一般留空，有密码框才填；urls 填三个格式网址。都不脑补。",
      forwardPlan: {
        startPeriod: "",
        mode: "continue",
        keepCodes: true,
        autoRegenerate: false,
        resetAt: "",
        note: "下一期启动转发；默认继续转发；号码不变"
      }
    };
  }

  function readConfigFile() {
    try {
      if (!fs.existsSync(urlsFile)) return defaultConfig();
      const raw = JSON.parse(fs.readFileSync(urlsFile, "utf8"));
      const base = defaultConfig();
      const urls = Object.assign({}, base.urls, (raw && raw.urls) || {});
      const fpIn = (raw && raw.forwardPlan && typeof raw.forwardPlan === "object") ? raw.forwardPlan : {};
      const fpBase = base.forwardPlan || {};
      return {
        active: String((raw && raw.active) || base.active || "1"),
        amount: String((raw && raw.amount) != null ? raw.amount : ""),
        password: String((raw && raw.password) != null ? raw.password : ""),
        urls: {
          "1": String(urls["1"] || ""),
          "2": String(urls["2"] || ""),
          "3": String(urls["3"] || "")
        },
        labels: Object.assign({}, base.labels, (raw && raw.labels) || {}),
        note: String((raw && raw.note) || base.note),
        forwardPlan: {
          startPeriod: String(fpIn.startPeriod != null ? fpIn.startPeriod : (fpBase.startPeriod || "")),
          mode: String(fpIn.mode || fpBase.mode || "continue"),
          keepCodes: fpIn.keepCodes !== false,
          autoRegenerate: fpIn.autoRegenerate === true,
          resetAt: String(fpIn.resetAt || fpBase.resetAt || ""),
          note: String(fpIn.note || fpBase.note || "")
        }
      };
    } catch (err) {
      const cfg = defaultConfig();
      cfg._readError = String(err && err.message || err);
      return cfg;
    }
  }

  function writeConfigFile(cfg) {
    const fp = (cfg.forwardPlan && typeof cfg.forwardPlan === "object") ? cfg.forwardPlan : (defaultConfig().forwardPlan || {});
    const next = {
      active: String(cfg.active || "1"),
      amount: String(cfg.amount != null ? cfg.amount : ""),
      password: String(cfg.password != null ? cfg.password : ""),
      urls: {
        "1": String((cfg.urls && cfg.urls["1"]) || ""),
        "2": String((cfg.urls && cfg.urls["2"]) || ""),
        "3": String((cfg.urls && cfg.urls["3"]) || "")
      },
      labels: Object.assign({}, defaultConfig().labels, cfg.labels || {}),
      note: String(cfg.note || defaultConfig().note),
      forwardPlan: {
        startPeriod: String(fp.startPeriod || ""),
        mode: String(fp.mode || "continue"),
        keepCodes: fp.keepCodes !== false,
        autoRegenerate: fp.autoRegenerate === true,
        resetAt: String(fp.resetAt || ""),
        note: String(fp.note || "")
      }
    };
    const tmp = urlsFile + ".tmp-" + process.pid;
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2) + "\n", "utf8");
    fs.renameSync(tmp, urlsFile);
    return next;
  }

  function jobDir(jobId) {
    return path.join(jobsDir, String(jobId || ""));
  }

  function statusPath(jobId) {
    return path.join(jobDir(jobId), "status.json");
  }

  function readStatus(jobId) {
    const p = statusPath(jobId);
    if (!fs.existsSync(p)) return null;
    try {
      return JSON.parse(fs.readFileSync(p, "utf8"));
    } catch (_) {
      return null;
    }
  }

  function writeStatus(jobId, status) {
    ensureJobsDir();
    const dir = jobDir(jobId);
    fs.mkdirSync(dir, { recursive: true });
    const next = Object.assign({}, status, { updatedAt: isoNow() });
    const p = statusPath(jobId);
    const tmp = p + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2) + "\n", "utf8");
    fs.renameSync(tmp, p);
    return next;
  }

  function makeJobId() {
    return "ft-" + Date.now().toString(36) + "-" + crypto.randomBytes(3).toString("hex");
  }

  function validatePages(pages) {
    if (!Array.isArray(pages) || !pages.length) {
      throw new Error("pages 不能为空");
    }
    const out = [];
    for (let i = 0; i < pages.length; i++) {
      const page = pages[i] || {};
      const index = Math.floor(Number(page.index));
      if (!Number.isFinite(index) || index < 1) throw new Error("第 " + (i + 1) + " 项页号非法");
      let codes = Array.isArray(page.codes) ? page.codes.map(String) : [];
      let attackText = String(page.attackText || "").trim();
      if (!codes.length && attackText) {
        const body = attackText.endsWith("各") ? attackText.slice(0, -1) : attackText;
        codes = body.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
      }
      if (!codes.length) throw new Error("第 " + index + " 页没有码");
      for (let c = 0; c < codes.length; c++) {
        if (!/^\d{4}$/.test(codes[c])) throw new Error("第 " + index + " 页含非四位码");
      }
      const rebuilt = codes.join(",") + "各";
      if (attackText) {
        if (!attackText.endsWith("各")) throw new Error("第 " + index + " 页攻击语未以「各」结尾");
        if (attackText !== rebuilt) throw new Error("第 " + index + " 页原文与码表不一致（不脑补）");
      } else {
        attackText = rebuilt;
      }
      const count = Number(page.count != null ? page.count : codes.length);
      if (count !== codes.length) throw new Error("第 " + index + " 页 count 与码表不一致");
      out.push({ index, count: codes.length, codes, attackText });
    }
    out.sort((a, b) => a.index - b.index);
    return out;
  }

  function writePagesFile(filePath, pages) {
    const lines = [];
    for (const page of pages) {
      lines.push("#page=" + page.index);
      lines.push(page.attackText);
    }
    fs.writeFileSync(filePath, lines.join("\n") + "\n", "utf8");
  }

  function parseLogProgress(logText, totalPages, pageIndexes) {
    const okSet = new Set();
    const failMap = new Map();
    const alertSet = new Set();
    const alertTips = [];
    const lines = String(logText || "").split(/\r?\n/);
    let failedPagesCsv = null;
    let allDone = false;
    let partialDone = false;
    let abortReason = "";

    for (const line of lines) {
      let m = line.match(/\bSEND_OK\s+page=(\d+)\b/);
      if (m) okSet.add(Number(m[1]));
      m = line.match(/^PAGE_OK_TIP\s+第(\d+)页/);
      if (m) okSet.add(Number(m[1]));
      m = line.match(/^PAGE_FAIL_TIP\s+第(\d+)页(?:转发失败|继续转发仍失败)[：:](.*)$/);
      if (m) {
        const idx = Number(m[1]);
        const reason = String(m[2] || "").trim() || "转发失败";
        failMap.set(idx, reason);
        okSet.delete(idx);
      }
      m = line.match(/^ALERT_PAGE\s+预警：第(\d+)页/);
      if (m) {
        alertSet.add(Number(m[1]));
        alertTips.push(line.replace(/^ALERT_PAGE\s+/, "").trim());
      }
      m = line.match(/^ALERT_TIP\s+(.+)$/);
      if (m) alertTips.push(m[1].trim());
      m = line.match(/^FAILED_PAGES=(.*)$/);
      if (m) failedPagesCsv = String(m[1] || "").trim();
      if (/\bALL_DONE\b/.test(line)) allDone = true;
      if (/\bPARTIAL_DONE\b/.test(line)) partialDone = true;
      m = line.match(/^BATCH_ABORT\s+reason=(.+)$/);
      if (m) abortReason = String(m[1] || "").trim();
    }

    if (failedPagesCsv != null) {
      const parts = failedPagesCsv ? failedPagesCsv.split(/[,\s]+/).filter(Boolean) : [];
      const csvSet = new Set(parts.map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0));
      for (const idx of csvSet) {
        if (!failMap.has(idx)) failMap.set(idx, "转发失败");
        okSet.delete(idx);
      }
      // 终态 CSV 以 FAILED_PAGES= 为准补齐失败集
      if (allDone || partialDone) {
        for (const idx of pageIndexes) {
          if (csvSet.has(idx)) continue;
          if (!okSet.has(idx) && !failMap.has(idx)) okSet.add(idx);
        }
      }
    }

    const failedPages = Array.from(failMap.keys()).sort((a, b) => a - b);
    const okPages = Array.from(okSet).filter((n) => !failMap.has(n)).sort((a, b) => a - b);
    const failTips = failedPages.map((p) => "第" + p + "页转发失败：" + (failMap.get(p) || "转发失败"));
    const failReasons = {};
    for (const p of failedPages) failReasons[String(p)] = failMap.get(p) || "转发失败";
    const alertPages = Array.from(alertSet).sort((a, b) => a - b);
    const done = okPages.length + failedPages.length;
    return {
      okPages,
      failedPages,
      failTips,
      failReasons,
      alertPages,
      alertTips: alertTips.filter(Boolean),
      done: Math.min(done, totalPages),
      okCount: okPages.length,
      failCount: failedPages.length,
      allDone,
      partialDone,
      abortReason
    };
  }

  function refreshStatusFromLog(jobId, baseStatus) {
    const st = Object.assign({}, baseStatus || readStatus(jobId) || {});
    const logFile = path.join(jobDir(jobId), "robot.log");
    let logText = "";
    try {
      if (fs.existsSync(logFile)) logText = fs.readFileSync(logFile, "utf8");
    } catch (_) {}
    const pageIndexes = Array.isArray(st.pageIndexes) ? st.pageIndexes : [];
    const total = Number(st.total || pageIndexes.length || 0);
    const prog = parseLogProgress(logText, total, pageIndexes);
    st.okPages = prog.okPages;
    st.failedPages = prog.failedPages;
    st.failTips = prog.failTips;
    st.failReasons = prog.failReasons;
    st.alertPages = prog.alertPages.length ? prog.alertPages : prog.failedPages.slice();
    st.alertTips = prog.alertTips;
    st.done = prog.done;
    st.okCount = prog.okCount;
    st.failCount = prog.failCount;
    st.retrying = false;
    if (prog.abortReason && st.status === "running") {
      st.message = "中止：" + prog.abortReason;
    } else if (st.status === "running") {
      st.message = "发送中 " + st.done + "/" + total
        + (st.failCount ? ("｜失败 " + st.failCount) : "")
        + (st.okCount ? ("｜成功 " + st.okCount) : "");
    }
    return st;
  }

  function markStoppedIncomplete(st) {
    const pageIndexes = Array.isArray(st.pageIndexes) ? st.pageIndexes : [];
    const okSet = new Set(Array.isArray(st.okPages) ? st.okPages : []);
    const failMap = Object.assign({}, st.failReasons || {});
    for (const idx of pageIndexes) {
      if (okSet.has(idx)) continue;
      if (!failMap[String(idx)]) {
        failMap[String(idx)] = "任务中断未确认";
      }
    }
    const failedPages = Object.keys(failMap).map(Number).filter((n) => Number.isFinite(n)).sort((a, b) => a - b);
    const failTips = failedPages.map((p) => "第" + p + "页转发失败：" + (failMap[String(p)] || "任务中断未确认"));
    st.status = "stopped";
    st.failedPages = failedPages;
    st.failTips = failTips;
    st.failReasons = failMap;
    st.failCount = failedPages.length;
    st.okPages = Array.from(okSet).filter((n) => !failMap[String(n)]).sort((a, b) => a - b);
    st.okCount = st.okPages.length;
    st.done = st.okCount + st.failCount;
    st.alertPages = failedPages.slice();
    st.alertTips = [];
    st.message = failedPages.length
      ? ("已停止｜没转成功：第" + failedPages.join("页、第") + "页")
      : "已停止";
    st.retrying = false;
    return st;
  }

  function finalizeJob(jobId, exitCode) {
    let st = readStatus(jobId) || { jobId, ok: true };
    const live = liveJobs.get(jobId);
    const stopRequested = !!(live && live.stopRequested) || st.stopRequested === true;
    st = refreshStatusFromLog(jobId, st);
    if (stopRequested) {
      st = markStoppedIncomplete(st);
    } else if (Number(exitCode) === 0 || st.allDone) {
      st.status = "done";
      st.message = st.failCount
        ? ("完成｜没转成功：第" + st.failedPages.join("页、第") + "页")
        : ("全部完成｜成功 " + st.okCount + "/" + st.total);
      if (!st.failCount && Array.isArray(st.pageIndexes)) {
        st.okPages = st.pageIndexes.slice();
        st.okCount = st.okPages.length;
        st.done = st.total;
      }
    } else if (st.partialDone || (Array.isArray(st.failedPages) && st.failedPages.length)) {
      st.status = "done";
      st.message = "部分完成｜没转成功：第" + st.failedPages.join("页、第") + "页｜可点「继续发失败页」";
    } else {
      st.status = "error";
      st.message = st.message || ("机器人退出码 " + exitCode);
      if (!st.failCount && Array.isArray(st.pageIndexes) && st.pageIndexes.length) {
        st = markStoppedIncomplete(st);
        st.status = "error";
        st.message = "发送异常退出｜退出码 " + exitCode;
      }
    }
    st.ok = true;
    writeStatus(jobId, st);
    liveJobs.delete(jobId);
  }

  function startRobot(jobId, meta) {
    const dir = jobDir(jobId);
    const pagesPath = path.join(dir, "pages.txt");
    const urlsPath = path.join(dir, "urls.json");
    const logPath = path.join(dir, "robot.log");
    try {
      fs.writeFileSync(logPath, "JOB_START " + jobId + " slot=" + meta.slot
        + " pages=" + meta.pageFrom + "-" + meta.pageTo + "\n", "utf8");
    } catch (_) {}

    const args = [
      robotScript,
      "--page-text-file", pagesPath,
      "--urls-file", urlsPath,
      "--amount", String(meta.amount),
      "--slot", String(meta.slot),
      "--page-from", String(meta.pageFrom),
      "--page-to", String(meta.pageTo),
      "--live",
      "--headless",
      "--sleep", "1.0"
    ];
    const child = spawn(pythonBin, args, {
      cwd: rootDir,
      env: Object.assign({}, process.env, { FOUR_THROW_JOB_LOG: logPath }),
      stdio: ["ignore", "pipe", "pipe"],
      detached: true
    });
    liveJobs.set(jobId, { child, stopRequested: false });

    const append = (buf) => {
      try { fs.appendFileSync(logPath, buf); } catch (_) {}
    };
    child.stdout.on("data", append);
    child.stderr.on("data", append);
    child.on("error", (err) => {
      let st = readStatus(jobId) || { jobId, ok: true };
      st.status = "error";
      st.message = "启动失败：" + String(err && err.message || err);
      writeStatus(jobId, st);
      liveJobs.delete(jobId);
    });
    child.on("exit", (code) => {
      try {
        finalizeJob(jobId, code == null ? 1 : code);
      } catch (err) {
        writeStatus(jobId, {
          ok: true,
          jobId,
          status: "error",
          message: "收尾失败：" + String(err && err.message || err),
          done: 0,
          total: 0,
          okCount: 0,
          failedPages: [],
          failTips: []
        });
        liveJobs.delete(jobId);
      }
    });
  }

  async function handleConfig(req, res, method) {
    if (method === "GET") {
      const cfg = readConfigFile();
      sendJson(res, 200, Object.assign({ ok: true }, cfg));
      return;
    }
    if (method === "POST") {
      let payload;
      try {
        payload = JSON.parse((await readRequestBody(req)) || "{}");
      } catch (err) {
        sendJson(res, 400, { ok: false, error: "请求格式无效：" + String(err && err.message || err) });
        return;
      }
      const amount = String(payload.amount != null ? payload.amount : "").trim();
      if (amount && !/^\d+(\.\d+)?$/.test(amount)) {
        sendJson(res, 400, { ok: false, error: "金额只能是数字，例如 0.5" });
        return;
      }
      const prev = readConfigFile();
      const merged = writeConfigFile({
        active: payload.active != null ? payload.active : prev.active,
        amount: payload.amount != null ? amount : prev.amount,
        password: payload.password != null ? payload.password : prev.password,
        urls: Object.assign({}, prev.urls, payload.urls || {}),
        labels: Object.assign({}, prev.labels, payload.labels || {}),
        note: payload.note != null ? payload.note : prev.note,
        forwardPlan: payload.forwardPlan != null ? Object.assign({}, prev.forwardPlan || {}, payload.forwardPlan) : prev.forwardPlan
      });
      sendJson(res, 200, Object.assign({ ok: true }, merged));
      return;
    }
    sendJson(res, 405, { ok: false, error: "Method Not Allowed" });
  }

  async function handleSend(req, res) {
    let payload;
    try {
      payload = JSON.parse((await readRequestBody(req)) || "{}");
    } catch (err) {
      sendJson(res, 400, { ok: false, error: "请求格式无效：" + String(err && err.message || err) });
      return;
    }
    try {
      for (const [id, live] of liveJobs.entries()) {
        if (live && live.child && !live.child.killed) {
          const st = readStatus(id);
          if (st && (st.status === "running" || st.status === "queued")) {
            sendJson(res, 409, { ok: false, error: "已有任务进行中：" + id + "，先停止再发" });
            return;
          }
        }
      }

      const pages = validatePages(payload.pages);
      const slot = String(payload.slot || (payload.config && payload.config.active) || "1");
      let amount = String(payload.amount != null ? payload.amount : "").trim();
      const cfgIn = payload.config && typeof payload.config === "object" ? payload.config : {};
      if (!amount) amount = String(cfgIn.amount || "").trim();
      if (!amount) throw new Error("金额为空（服务不脑补金额）");
      if (!/^\d+(\.\d+)?$/.test(amount)) throw new Error("金额只能是数字，例如 1 或 0.2");

      const fileCfg = readConfigFile();
      const urls = Object.assign({}, fileCfg.urls, cfgIn.urls || {});
      const url = String(urls[slot] || "").trim();
      if (!url) throw new Error("网址为空：先填网址并保存");

      const pageFrom = Math.min.apply(null, pages.map((p) => p.index));
      const pageTo = Math.max.apply(null, pages.map((p) => p.index));
      const jobId = makeJobId();
      ensureJobsDir();
      const dir = jobDir(jobId);
      fs.mkdirSync(dir, { recursive: true });

      const jobUrls = {
        active: slot,
        amount,
        password: String(cfgIn.password != null ? cfgIn.password : fileCfg.password || ""),
        urls: {
          "1": String(urls["1"] || ""),
          "2": String(urls["2"] || ""),
          "3": String(urls["3"] || "")
        },
        labels: Object.assign({}, fileCfg.labels),
        note: fileCfg.note
      };
      // 接单时顺带落盘主配置（金额+当前槽网址），与面板「保存」一致
      writeConfigFile(Object.assign({}, fileCfg, {
        active: slot,
        amount,
        urls: jobUrls.urls,
        password: jobUrls.password
      }));
      fs.writeFileSync(path.join(dir, "urls.json"), JSON.stringify(jobUrls, null, 2) + "\n", "utf8");
      writePagesFile(path.join(dir, "pages.txt"), pages);

      const status = {
        ok: true,
        jobId,
        status: "running",
        done: 0,
        total: pages.length,
        okCount: 0,
        failCount: 0,
        failedPages: [],
        failTips: [],
        alertPages: [],
        alertTips: [],
        retrying: false,
        failReasons: {},
        okPages: [],
        pageIndexes: pages.map((p) => p.index),
        message: "已接单，发送中…",
        slot,
        amount,
        pageFrom,
        pageTo,
        stopRequested: false
      };
      writeStatus(jobId, status);
      startRobot(jobId, { slot, amount, pageFrom, pageTo });
      sendJson(res, 200, { ok: true, jobId, status: "running", total: pages.length });
    } catch (err) {
      sendJson(res, 422, { ok: false, error: String(err && err.message || err) });
    }
  }

  function handleStatus(req, res, parsedUrl) {
    const jobId = String(parsedUrl.searchParams.get("jobId") || "").trim();
    if (!jobId) {
      // 无 jobId：返回最近一个任务（若有）
      ensureJobsDir();
      let latest = null;
      try {
        const names = fs.readdirSync(jobsDir).filter((n) => n.indexOf("ft-") === 0);
        names.sort();
        for (let i = names.length - 1; i >= 0; i--) {
          const st = readStatus(names[i]);
          if (st) { latest = st; break; }
        }
      } catch (_) {}
      if (!latest) {
        sendJson(res, 200, {
          ok: true,
          jobId: "",
          status: "idle",
          done: 0,
          total: 0,
          okCount: 0,
          failedPages: [],
          failTips: [],
          alertPages: [],
          alertTips: [],
          message: "无任务"
        });
        return;
      }
      if (latest.status === "running" || latest.status === "queued") {
        latest = refreshStatusFromLog(latest.jobId, latest);
        writeStatus(latest.jobId, latest);
      }
      sendJson(res, 200, latest);
      return;
    }
    let st = readStatus(jobId);
    if (!st) {
      sendJson(res, 404, { ok: false, error: "找不到任务 " + jobId });
      return;
    }
    if (st.status === "running" || st.status === "queued") {
      st = refreshStatusFromLog(jobId, st);
      writeStatus(jobId, st);
    }
    sendJson(res, 200, st);
  }

  async function handleStop(req, res) {
    let payload = {};
    try {
      payload = JSON.parse((await readRequestBody(req)) || "{}");
    } catch (_) {
      payload = {};
    }
    const jobId = String(payload.jobId || "").trim();
    if (!jobId) {
      sendJson(res, 400, { ok: false, error: "缺少 jobId" });
      return;
    }
    let st = readStatus(jobId);
    if (!st) {
      sendJson(res, 404, { ok: false, error: "找不到任务 " + jobId });
      return;
    }
    const live = liveJobs.get(jobId);
    if (live) live.stopRequested = true;
    st.stopRequested = true;
    writeStatus(jobId, st);

    if (live && live.child && live.child.pid) {
      try { process.kill(-live.child.pid, "SIGTERM"); } catch (_) {
        try { live.child.kill("SIGTERM"); } catch (_2) {}
      }
      setTimeout(() => {
        try {
          if (live.child && !live.child.killed) process.kill(-live.child.pid, "SIGKILL");
        } catch (_) {
          try { if (live.child) live.child.kill("SIGKILL"); } catch (_2) {}
        }
      }, 2500);
    } else if (st.status === "running" || st.status === "queued") {
      st = refreshStatusFromLog(jobId, st);
      st = markStoppedIncomplete(st);
      writeStatus(jobId, st);
    }

    sendJson(res, 200, {
      ok: true,
      jobId,
      message: "已请求停止。",
      status: "stopping"
    });
  }

  async function handle(req, res, pathname, method, parsedUrl) {
    if (pathname === "/four-throw-config" && (method === "GET" || method === "POST")) {
      await handleConfig(req, res, method);
      return true;
    }
    if (pathname === "/four-throw-send" && method === "POST") {
      await handleSend(req, res);
      return true;
    }
    if (pathname === "/four-throw-status" && method === "GET") {
      handleStatus(req, res, parsedUrl);
      return true;
    }
    if (pathname === "/four-throw-stop" && method === "POST") {
      await handleStop(req, res);
      return true;
    }
    return false;
  }

  return {
    handle,
    paths: [
      "GET/POST /four-throw-config",
      "POST /four-throw-send",
      "GET /four-throw-status",
      "POST /four-throw-stop"
    ]
  };
}

module.exports = { createFourThrowHttp };
