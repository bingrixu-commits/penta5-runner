'use strict';

// 按框架的 AI 启停开关接口。只允许在预置作用域表内切换布尔状态，
// 不接受任意命令、不接受路径、不触碰密钥与预测账本。
const path = require('path');
const {execFile} = require('child_process');
const webSecurity = require('./web_security');

const ENDPOINT = '/api/ai-scope';
const MAX_BODY = 512;
const ACTIONS = ['start', 'stop'];
const SCOPE_RE = /^[a-z][a-z0-9]{1,15}$/;

function send(res, status, data) {
  if (res.writableEnded || res.destroyed) return;
  res.writeHead(status, {'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store', 'Pragma': 'no-cache',
    'X-Content-Type-Options': 'nosniff', 'Vary': 'Authorization, Cookie'});
  res.end(JSON.stringify(data));
}

function body(req) {
  return new Promise((resolve, reject) => {
    let size = 0, chunks = [], done = false;
    const fail = status => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      chunks = [];
      req.resume();
      reject(Object.assign(new Error('invalid-body'), {status}));
    };
    const timer = setTimeout(() => fail(408), 5000);
    req.on('data', chunk => {
      if (done) return;
      size += Buffer.byteLength(chunk);
      if (size > MAX_BODY) { fail(413); return; }
      chunks.push(Buffer.from(chunk));
    });
    req.on('end', () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch (_) { reject(Object.assign(new Error('invalid-json'), {status: 400})); }
    });
    req.on('error', () => fail(400));
    req.on('aborted', () => fail(400));
  });
}

function createAiScopeHandler(options) {
  const root = path.resolve(options.root);
  const authRoleForRequest = options.authRoleForRequest;
  if (typeof authRoleForRequest !== 'function') throw new TypeError('scope control requires role auth');
  const execute = options.execFileImpl || execFile;
  const script = path.join(root, 'scripts', 'ai_scope_control.py');
  let busy = false;

  function invoke(args) {
    return new Promise(resolve => {
      try {
        execute('python3', [script].concat(args), {cwd: root, shell: false, timeout: 8000,
          maxBuffer: 65536, encoding: 'utf8', windowsHide: true}, (error, stdout) => {
          let value = null;
          try { value = JSON.parse(stdout); } catch (_) {}
          resolve({error, value});
        });
      } catch (_) { resolve({error: new Error('unavailable'), value: null}); }
    });
  }

  function publicRows(value) {
    const scopes = value && Array.isArray(value.scopes) ? value.scopes : null;
    if (!scopes) throw new Error('invalid-scope-state');
    return scopes.map(row => ({
      scope: String(row.scope || ''),
      label: String(row.label || ''),
      enabled: row.enabled === true,
      own_key: row.own_key === true,
    })).filter(row => SCOPE_RE.test(row.scope));
  }

  return async function handleAiScopeRequest(req, res) {
    let url;
    try { url = new URL(req.url || '/', 'http://localhost'); } catch (_) { return false; }
    if (url.pathname !== ENDPOINT) return false;
    if (!webSecurity.guardRequest(req, res)) return true;
    const role = authRoleForRequest(req);
    if (role !== 'admin' && role !== 'viewer') { send(res, 401, {ok: false, error: 'authentication-required'}); return true; }
    if (!['GET', 'POST'].includes(req.method)) {
      res.setHeader('Allow', 'GET, POST');
      send(res, 405, {ok: false, error: 'method-not-allowed'});
      return true;
    }
    if (url.search) { send(res, 400, {ok: false, error: 'unexpected-query'}); return true; }

    if (req.method === 'GET') {
      const result = await invoke(['status']);
      try {
        if (result.error) throw result.error;
        send(res, 200, {ok: true, scopes: publicRows(result.value), can_control: role === 'admin'});
      } catch (_) { send(res, 503, {ok: false, error: 'scope-unavailable'}); }
      return true;
    }

    if (role !== 'admin') { send(res, 403, {ok: false, error: 'admin-required'}); return true; }
    if (!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(String(req.headers['content-type'] || ''))) {
      send(res, 415, {ok: false, error: 'json-required'}); return true;
    }
    let input;
    try { input = await body(req); }
    catch (error) { send(res, error.status || 400, {ok: false, error: 'invalid-request'}); return true; }
    if (!input || typeof input !== 'object' || Array.isArray(input)
        || Object.keys(input).sort().join(',') !== 'action,scope'
        || !ACTIONS.includes(input.action)
        || typeof input.scope !== 'string' || !SCOPE_RE.test(input.scope)) {
      send(res, 400, {ok: false, error: 'invalid-request'}); return true;
    }
    if (busy) { send(res, 503, {ok: false, error: 'scope-busy'}); return true; }
    busy = true;
    try {
      const result = await invoke([input.action, '--scope', input.scope]);
      if (result.error) { send(res, 503, {ok: false, error: 'scope-unavailable'}); return true; }
      const entry = result.value && result.value.entry;
      if (!entry || entry.enabled !== (input.action === 'start')) throw new Error('invalid-result');
      const after = await invoke(['status']);
      send(res, 200, {ok: true, scope: input.scope, enabled: entry.enabled,
        scopes: publicRows(after.value), can_control: true});
    } catch (_) { send(res, 503, {ok: false, error: 'scope-unavailable'}); }
    finally { busy = false; }
    return true;
  };
}

module.exports = {createAiScopeHandler, ENDPOINT};
