'use strict';
const net = require('net');

function trustedProxy(req) {
  return ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket?.remoteAddress);
}

function clientAddress(req) {
  if (trustedProxy(req)) {
    const forwarded = String(req.headers['x-real-ip'] || '').trim();
    if (net.isIP(forwarded)) return forwarded;
  }
  return req.socket?.remoteAddress || 'unknown';
}

function isHttps(req) {
  return Boolean(req.socket?.encrypted) || (trustedProxy(req) && req.headers['x-forwarded-proto'] === 'https');
}

function reject(res, status, message) {
  res.writeHead(status, {'Content-Type':'application/json; charset=utf-8', 'Cache-Control':'no-store'});
  res.end(JSON.stringify({ok:false,error:message}));
  return false;
}

function guardRequest(req, res) {
  res.setHeader('X-Content-Type-Options','nosniff');
  res.setHeader('X-Frame-Options','SAMEORIGIN');
  res.setHeader('Referrer-Policy','same-origin');
  res.setHeader('Cross-Origin-Resource-Policy','same-origin');
  let parsed;
  try { parsed = new URL(req.url || '/', 'http://localhost'); } catch { return reject(res,400,'invalid request target'); }
  if (!['GET','HEAD','OPTIONS','POST','PUT','PATCH','DELETE'].includes(req.method)) return reject(res,405,'method not allowed');
  const statefulRefresh = req.method === 'GET' && ['1','true'].includes(parsed.searchParams.get('refresh'))
    && ['/stable-math-filter','/adaptive-exclusion','/exclusion-model'].includes(parsed.pathname);
  if (statefulRefresh || !['GET','HEAD','OPTIONS'].includes(req.method)) {
    if (req.headers['sec-fetch-site'] === 'cross-site') return reject(res,403,'cross-site request rejected');
    const origin = req.headers.origin;
    if (origin) {
      try {
        const expected = new URL(`${isHttps(req) ? 'https' : 'http'}://${req.headers.host}`);
        const actual = new URL(origin);
        if (actual.origin !== expected.origin) return reject(res,403,'origin mismatch');
      } catch { return reject(res,403,'invalid origin'); }
    }
  }
  return true;
}

function failRequest(res) {
  if (res.headersSent) { res.destroy(); return; }
  reject(res,500,'request failed');
}

module.exports = {clientAddress,isHttps,guardRequest,failRequest};
