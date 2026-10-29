'use strict';

// This route changes only the manual API permission record. It cannot run a
// model, pass a shell command, read a provider key, or modify prediction seals.
const path = require('path');
const fs = require('fs');
const {execFile} = require('child_process');
const webSecurity = require('./web_security');

const ENDPOINT = '/api/ai-control';
const MAX_BODY = 1024;
const STATE_KEYS = ['version', 'mode', 'enabled', 'generation', 'updated_at'];

function publicState(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || value.version !== 1 || value.mode !== 'manual' || typeof value.enabled !== 'boolean'
      || !Number.isSafeInteger(value.generation) || value.generation < 0
      || !(value.updated_at === null || (typeof value.updated_at === 'string'
        && value.updated_at.length <= 64 && /^\d{4}-\d{2}-\d{2}T/.test(value.updated_at)
        && /(?:Z|[+-]\d{2}:\d{2})$/.test(value.updated_at) && Number.isFinite(Date.parse(value.updated_at))))) {
    throw new Error('invalid-control-state');
  }
  const result = {};
  STATE_KEYS.forEach(key => { result[key] = value[key]; });
  return result;
}

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
      reject(Object.assign(new Error('invalid-control-body'), {status}));
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
      catch (_) { reject(Object.assign(new Error('invalid-control-json'), {status: 400})); }
    });
    req.on('error', () => fail(400));
    req.on('aborted', () => fail(400));
  });
}

function createAiControlHandler(options) {
  const root = path.resolve(options.root);
  const authRoleForRequest = options.authRoleForRequest;
  if (typeof authRoleForRequest !== 'function') throw new TypeError('AI control requires role authentication');
  const execute = options.execFileImpl || execFile;
  const script = path.join(root, 'scripts', 'ai_api_control.py');
  const isInstalling = options.isInstalling || (() => fs.existsSync(path.join(root, 'data', 'ai_api_control_installing')));
  let mutationBusy = false;

  function invoke(action, generation) {
    const args = [script, action];
    if (generation !== undefined) args.push('--expected-generation', String(generation));
    return new Promise(resolve => {
      try { execute('python3', args, {cwd: root, shell: false, timeout: 8000,
        maxBuffer: 16384, encoding: 'utf8', windowsHide: true}, (error, stdout) => {
        let value = null;
        try { value = JSON.parse(stdout); } catch (_) {}
        resolve({error, value});
      }); } catch (_) { resolve({error: new Error('control-unavailable'), value: null}); }
    });
  }

  return async function handleAiControlRequest(req, res) {
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
      const result = await invoke('status');
      try {
        if (result.error) throw result.error;
        send(res, 200, {ok: true, state: publicState(result.value), can_control: role === 'admin'});
      } catch (_) { send(res, 503, {ok: false, error: 'control-unavailable'}); }
      return true;
    }
    if (role !== 'admin') {
      send(res, 403, {ok: false, error: 'admin-required'});
      return true;
    }
    if (!/^application\/json(?:\s*;\s*charset=utf-8)?\s*$/i.test(String(req.headers['content-type'] || ''))) {
      send(res, 415, {ok: false, error: 'json-required'});
      return true;
    }
    let input;
    try { input = await body(req); }
    catch (error) { send(res, error.status || 400, {ok: false, error: 'invalid-control-request'}); return true; }
    if (!input || typeof input !== 'object' || Array.isArray(input)
        || Object.keys(input).sort().join(',') !== 'action,expected_generation'
        || !['start', 'stop'].includes(input.action)
        || !Number.isSafeInteger(input.expected_generation) || input.expected_generation < 0
        || input.expected_generation >= Number.MAX_SAFE_INTEGER) {
      send(res, 400, {ok: false, error: 'invalid-control-request'});
      return true;
    }
    if (input.action === 'start' && isInstalling()) {
      send(res, 503, {ok: false, error: 'control-busy'});
      return true;
    }
    if (mutationBusy) { send(res, 503, {ok: false, error: 'control-busy'}); return true; }
    mutationBusy = true;
    try {
      const result = await invoke(input.action, input.expected_generation);
      if (result.error) {
        if (result.value && result.value.error === 'generation-conflict') {
          try {
            send(res, 409, {ok: false, error: 'generation-conflict', state: publicState(result.value.state), can_control: true});
            return true;
          } catch (_) {}
        }
        send(res, 503, {ok: false, error: 'control-unavailable'});
        return true;
      }
      const state = publicState(result.value);
      if (state.enabled !== (input.action === 'start') || state.generation < input.expected_generation
          || state.generation > input.expected_generation + 1) throw new Error('invalid-control-result');
      send(res, 200, {ok: true, state, can_control: true});
    } catch (_) { send(res, 503, {ok: false, error: 'control-unavailable'}); }
    finally { mutationBusy = false; }
    return true;
  };
}

module.exports = {createAiControlHandler};
