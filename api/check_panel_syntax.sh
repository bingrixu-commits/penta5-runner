#!/bin/bash
set -e
node <<'NODE'
const fs=require('fs');
const html=fs.readFileSync('/root/penta5-runner/panel.html','utf8');
const scripts=[];
const re=/<script\b[^>]*>([\s\S]*?)<\/script>/gi;
let m;
while((m=re.exec(html))){
  const tag=m[0];
  if(/\ssrc\s*=/i.test(tag)) continue;
  scripts.push(m[1]);
}
console.log('script_blocks', scripts.length);
let failed=0;
scripts.forEach((code,i)=>{
  try{ new Function(code); }
  catch(e){ failed++; console.log('SYNTAX_FAIL', i+1, e.message); }
});
console.log(failed?('SYNTAX_FAILED '+failed):'SYNTAX_OK');
NODE
# also open panel through public path locally
curl -s -o /dev/null -w "local_panel=%{http_code}\n" http://127.0.0.1:3001/panel
curl -s -o /dev/null -w "local_health=%{http_code}\n" http://127.0.0.1:3001/health
