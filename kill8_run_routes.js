'use strict';

// Route that manually triggers the kill8 framework run script:
// settle previous period + seal next cold-form period + refresh panel.
// Mirrors ai_control_routes.js conventions (webSecurity guard + authRoleForRequest),
// but the action is long-running (real DeepSeek call), so it spawns the script
// detached in the background instead of waiting synchronously.
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const webSecurity = require('./web_security');

const ENDPOINT = '/api/kill8-run';
const LOG_REL = path.join('data', 'kill8_run.log');

function send(res, status, data) {
  if (res.writableEnded || res.destroyed) return;
  res.writeHead(status, {'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store', 'Pragma': 'no-cache',
    'X-Content-Type-Options': 'nosniff', 'Vary': 'Authorization, Cookie'});
  res.end(JSON.stringify(data));
}

function createKill8RunHandler(options) {
  const root = path.resolve(options.root);
  const authRoleForRequest = options.authRoleForRequest;
  if (typeof authRoleForRequest !== 'function') throw new TypeError('kill8 run requires role authentication');
  const script = path.join(root, 'scripts', 'run_kill8_tail.py');
  const logPath = path.join(root, LOG_REL);
  let running = false;
  let lastStart = null;
  let lastExitCode = null;

  function spawnRun() {
    const logFd = fs.openSync(logPath, 'a');
    fs.writeSync(logFd, '\n==== kill8 run start ' + new Date().toISOString() + ' ====\n');
    lastStart = new Date().toISOString();
    const child = spawn('python3', [script], {
      cwd: root,
      detached: true,
      stdio: ['ignore', logFd, logFd],
      env: process.env
    });
    child.unref();
    child.on('exit', (code) => {
      try {
        fs.writeSync(logFd, '==== kill8 run exit code=' + code + ' at ' + new Date().toISOString() + ' ====\n');
        fs.closeSync(logFd);
      } catch (_) {}
      running = false;
      lastExitCode = code;
    });
    child.on('error', (err) => {
      try {
        fs.writeSync(logFd, '==== kill8 run spawn error ' + String(err && err.message) + ' ====\n');
        fs.closeSync(logFd);
      } catch (_) {}
      running = false;
      lastExitCode = -1;
    });
    return child.pid;
  }

  return async function handleKill8RunRequest(req, res) {
    let url;
    try { url = new URL(req.url || '/', 'http://localhost'); }
    catch (_) { return false; }
    if (url.pathname !== ENDPOINT) return false;
    if (!webSecurity.guardRequest(req, res)) return true;
    const role = authRoleForRequest(req);
    if (role !== 'admin' && role !== 'viewer') {
      send(res, 401, {ok: false, error: 'authentication-required'});
      return true;
    }
    if (!['GET', 'POST'].includes(req.method)) {
      res.setHeader('Allow', 'GET, POST');
      send(res, 405, {ok: false, error: 'method-not-allowed'});
      return true;
    }
    if (url.search) {
      send(res, 400, {ok: false, error: 'unexpected-query'});
      return true;
    }
    if (req.method === 'GET') {
      send(res, 200, {ok: true, running: running, last_start: lastStart,
        last_exit_code: lastExitCode, can_run: role === 'admin'});
      return true;
    }
    // POST = trigger the run (a real paid AI call). Admin only.
    if (role !== 'admin') {
      send(res, 403, {ok: false, error: 'admin-required'});
      return true;
    }
    if (running) {
      send(res, 409, {ok: false, error: 'run-busy'});
      return true;
    }
    // Read optional position-exclusion body and persist it before spawning.
    let exclusions = null;
    try {
      let raw = '';
      for await (const chunk of req) { raw += chunk; }
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && parsed.exclusions) {
          exclusions = parsed.exclusions;
        }
      }
    } catch (_) {
      // ignore malformed body; run without exclusions
    }
    if (exclusions) {
      try {
        const exclPath = path.join(root, 'data', 'kill8_position_exclusions.json');
        fs.mkdirSync(path.dirname(exclPath), {recursive: true});
        const normalized = {};
        for (const k of ['q', 'b', 's', 'g']) {
          normalized[k] = String((exclusions && exclusions[k]) || '').replace(/[^0-9]/g, '');
        }
        fs.writeFileSync(exclPath, JSON.stringify(normalized, null, 2));
      } catch (err) {
        send(res, 500, {ok: false, error: 'exclusions-persist-failed'});
        return true;
      }
    }
    running = true;
    let pid;
    try {
      pid = spawnRun();
    } catch (err) {
      running = false;
      send(res, 503, {ok: false, error: 'run-unavailable'});
      return true;
    }
    send(res, 200, {ok: true, started: true, pid: pid, script: 'run_kill8_tail.py'});
    return true;
  };
}

module.exports = {createKill8RunHandler};
