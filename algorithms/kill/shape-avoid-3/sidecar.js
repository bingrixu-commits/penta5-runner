"use strict";
const { MODEL_ID, GROUP_N, WIDTH, refresh } = require("./engine");
const POLL_MS = Math.max(15000, Number(process.env.SHAPE_AVOID_POLL_MS || 60000));
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
    const j = state.joint || {};
    log("period="+period+" width="+WIDTH+" JOINT%="+j.rate120+"% ("+j.hit120+"/"+j.total120+") lower="+j.lower120+" long="+j.rateLong+"% shot="+state.shot);
    (state.groups||[]).forEach((g)=>{
      log("  组合"+g.group+" "+g.attack+" ["+(g.shapeKey==="shapeSize"?"大小":"单双")+"]");
    });
  }catch(e){
    log("tick error: "+(e&&e.message?e.message:String(e)));
  }finally{ busy=false; }
}
async function main(){
  log("start poll="+POLL_MS+"ms joint-AND groups="+GROUP_N);
  await tick(true);
  if(once){ log("once done"); process.exit(0); }
  setInterval(()=>tick(false), POLL_MS);
}
main().catch((e)=>{ console.error(e); process.exit(1); });
