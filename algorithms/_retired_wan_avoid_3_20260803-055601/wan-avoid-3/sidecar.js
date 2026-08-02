"use strict";
const { MODEL_ID, GROUP_N, refresh } = require("./engine");
const POLL_MS = Math.max(15000, Number(process.env.WAN_AVOID_POLL_MS || 60000));
const once = process.argv.includes("--once");
let lastPeriod = "";
let busy = false;
function log(msg){ console.log("["+new Date().toISOString()+"] "+MODEL_ID+" "+msg); }
async function tick(force){
  if(busy) return;
  busy = true;
  try{
    const state = await refresh();
    const period = String(state.latestPeriod||"");
    if(!force && period && period===lastPeriod){ log("skip same period "+period); return; }
    lastPeriod = period;
    log("period="+period+" groups="+GROUP_N+" shot="+state.shot);
    (state.groups||[]).forEach((g)=>{
      log("#"+g.group+" "+g.name+" "+g.attack+" r1000="+g.rateLong+"% ("+g.hitLong+"/"+g.totalLong+") lower1000="+g.lowerLong+" r120="+g.rate120+"%");
    });
  }catch(e){
    log("tick error: "+(e&&e.message?e.message:String(e)));
  }finally{ busy=false; }
}
async function main(){
  log("start poll="+POLL_MS+"ms groups="+GROUP_N+" longWindow=1000");
  await tick(true);
  if(once){ log("once done"); process.exit(0); }
  setInterval(()=>tick(false), POLL_MS);
}
main().catch((e)=>{ console.error(e); process.exit(1); });
