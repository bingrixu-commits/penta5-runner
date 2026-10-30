#!/usr/bin/env python3
"""给 penta5-runner 面板 panel.html 加「刮刮乐 · 单双大小」静态区块。"""
import os
import sys
from pathlib import Path

ROOT = Path('/root/penta5-runner')
PANEL = ROOT / 'panel.html'

STYLE = '''<style id="penta5-scratch-style">
#scratch-card{background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 18px 12px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.04);}
#scratch-card.sc-new{animation:scNewPulse 1.2s ease-out 2;}
@keyframes scNewPulse{0%{box-shadow:0 0 0 0 rgba(234,88,12,.45)}70%{box-shadow:0 0 0 12px rgba(234,88,12,0)}100%{box-shadow:0 0 0 0 rgba(234,88,12,0)}}
#scratch-card .sc-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px;}
#scratch-card .sc-title{font-size:16px;font-weight:800;color:#0f172a;letter-spacing:.3px;}
#scratch-card .sc-title small{font-size:12px;font-weight:600;color:#94a3b8;margin-left:6px;letter-spacing:0;}
#scratch-card .sc-meta{font-size:12px;font-weight:600;color:#64748b;}
#scratch-card .sc-meta b{color:#0f172a;}
#scratch-card .sc-all{border:0;background:none;padding:0 0 0 8px;margin:0;font-size:12px;font-weight:700;color:#0284c7;cursor:pointer;text-decoration:underline;}
#scratch-card .sc-all:hover{color:#0369a1;}
#scratch-card .sc-body{position:relative;border-radius:10px;overflow:hidden;}
#scratch-card .sc-board{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;padding:2px;}
#scratch-card .sc-tile{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:9px 4px 8px;text-align:center;min-width:0;}
#scratch-card .sc-pos{font-size:12px;font-weight:700;color:#94a3b8;margin-bottom:2px;white-space:nowrap;}
#scratch-card .sc-digit{font-size:30px;line-height:1.05;font-weight:900;color:#0f172a;}
#scratch-card .sc-badges{display:flex;justify-content:center;gap:6px;margin-top:7px;}
#scratch-card .sc-badge{font-size:12px;font-weight:800;line-height:1.6;border-radius:999px;padding:0 8px;}
#scratch-card .sc-big{background:#fee2e2;color:#b91c1c;}
#scratch-card .sc-small{background:#dbeafe;color:#1d4ed8;}
#scratch-card .sc-odd{background:#ede9fe;color:#6d28d9;}
#scratch-card .sc-even{background:#dcfce7;color:#15803d;}
#scratch-card .sc-shapes{display:flex;gap:8px;flex-wrap:wrap;padding:10px 2px 2px;}
#scratch-card .sc-shape{background:#f1f5f9;border:1px dashed #cbd5e1;border-radius:8px;padding:5px 10px;font-size:13px;font-weight:700;color:#334155;}
#scratch-card .sc-shape b{color:#0f172a;letter-spacing:2px;font-size:14px;margin-left:6px;}
#scratch-card .sc-shape em{font-style:normal;color:#94a3b8;font-size:12px;margin-left:9px;font-weight:600;letter-spacing:0;}
#scratch-card .sc-canvas{position:absolute;left:0;top:0;display:block;width:100%;height:100%;border-radius:10px;cursor:crosshair;touch-action:none;transition:opacity .4s ease;}
#scratch-card .sc-canvas.sc-done{opacity:0;pointer-events:none;}
#scratch-card .sc-hint{margin-top:8px;min-height:16px;font-size:12px;font-weight:600;color:#94a3b8;}
#scratch-card .sc-hint b{color:#0284c7;}
#scratch-card .sc-hint em{font-style:normal;color:#15803d;}
@media (max-width:560px){#scratch-card{padding:12px 12px 10px;}#scratch-card .sc-digit{font-size:24px;}#scratch-card .sc-badge{font-size:11px;padding:0 6px;}#scratch-card .sc-pos{font-size:11px;}}
</style>
'''

HTML = '''<!-- 刮刮乐 · 单双大小（每期开奖自动换卡；静态区块，无 START/END 标记，勿删） -->
<section id="scratch-card" aria-label="单双大小刮刮乐">
  <div class="sc-head">
    <div class="sc-title">🎫 刮刮乐 · 单双大小<small>每期开奖自动换一张</small></div>
    <div class="sc-meta">第 <b id="sc_period">—</b> 期 · <span id="sc_time">—</span><button type="button" class="sc-all" id="sc_all">全部刮开</button></div>
  </div>
  <div class="sc-body">
    <div class="sc-board" id="sc_board"></div>
    <div class="sc-shapes" id="sc_shapes"></div>
    <canvas class="sc-canvas" id="sc_canvas" aria-hidden="true"></canvas>
  </div>
  <div class="sc-hint" id="sc_hint"></div>
</section>
'''

SCRIPT = '''<script id="penta5-scratch-card">
(function(){
  'use strict';
  var INSTANCE_KEY='__PENTA5_SCRATCH_CARD__';
  var old=window[INSTANCE_KEY];
  if(old&&typeof old.sync==='function'){try{old.sync();}catch(error){}return;}

  var card=document.getElementById('scratch-card');
  var board=document.getElementById('sc_board');
  var shapes=document.getElementById('sc_shapes');
  var canvas=document.getElementById('sc_canvas');
  var hint=document.getElementById('sc_hint');
  var periodEl=document.getElementById('sc_period');
  var timeEl=document.getElementById('sc_time');
  var allBtn=document.getElementById('sc_all');
  if(!card||!board||!shapes||!canvas||!hint||!periodEl||!timeEl||!allBtn)return;
  var ctx=null;
  try{ctx=canvas.getContext('2d');}catch(error){ctx=null;}

  var dpr=Math.max(1,Math.min(2,Number(window.devicePixelRatio)||1));
  var key='';
  var drawing=false;
  var lastPoint=null;
  var moves=0;
  var revealed=false;
  var resizeTimer=null;
  var pulseTimer=null;
  var autoTimer=null;
  var POS=['千位','百位','十位','个位','第5球'];

  function text(id){var node=document.getElementById(id);return node?String(node.textContent||'').trim():'';}
  function digits(){var raw=text('disp_numbers').replace(/[^0-9]/g,'');var list=[];for(var i=0;i<raw.length&&list.length<5;i++)list.push(Number(raw.charAt(i)));return list;}
  function isBig(n){return n>=5;}
  function odd(n){return n%2===1;}
  function ds(n){return odd(n)?'单':'双';}
  function dx(n){return isBig(n)?'大':'小';}
  function count(list,test){var total=0;for(var i=0;i<list.length;i++){if(test(list[i]))total++;}return total;}

  function render(){
    var nums=digits();
    var period=text('disp_period');
    var stamp=text('disp_time');
    periodEl.textContent=period||'—';
    timeEl.textContent=stamp?stamp.replace(/^[0-9]{4}-[0-9]{2}-[0-9]{2}\s*/,'')+' 开奖':'—';
    var tiles='';
    for(var i=0;i<nums.length;i++){
      var n=nums[i];
      tiles+='<div class="sc-tile"><div class="sc-pos">'+(POS[i]||('第'+(i+1)+'球'))+'</div>'+
        '<div class="sc-digit">'+n+'</div>'+
        '<div class="sc-badges"><span class="sc-badge '+(isBig(n)?'sc-big':'sc-small')+'">'+dx(n)+'</span>'+
        '<span class="sc-badge '+(odd(n)?'sc-odd':'sc-even')+'">'+ds(n)+'</span></div></div>';
    }
    board.innerHTML=tiles;
    var four=nums.slice(0,4);
    var chips=[];
    if(four.length){
      chips.push('<div class="sc-shape">前四位单双形态<b>'+four.map(ds).join('')+'</b><em>单'+count(four,odd)+' 双'+count(four,function(n){return !odd(n);})+'</em></div>');
      chips.push('<div class="sc-shape">前四位大小形态<b>'+four.map(dx).join('')+'</b><em>大'+count(four,isBig)+' 小'+count(four,function(n){return !isBig(n);})+'</em></div>');
    }
    var hew=text('disp_hewei');
    if(/^[0-9]$/.test(hew)){var h=Number(hew);chips.push('<div class="sc-shape">前四位合尾<b>'+h+' · '+ds(h)+' · '+dx(h)+'</b></div>');}
    shapes.innerHTML=chips.join('');
    resetCover();
  }

  function sizeCanvas(){
    if(!ctx)return null;
    var body=card.querySelector('.sc-body');
    if(!body)return null;
    var rect=body.getBoundingClientRect();
    var w=Math.max(1,Math.round(rect.width));
    var h=Math.max(1,Math.round(rect.height));
    canvas.width=Math.round(w*dpr);
    canvas.height=Math.round(h*dpr);
    canvas.style.width=w+'px';
    canvas.style.height=h+'px';
    ctx.setTransform(dpr,0,0,dpr,0,0);
    return {w:w,h:h};
  }

  function paintCover(){
    if(!ctx)return;
    var dim=sizeCanvas();
    if(!dim)return;
    var w=dim.w,h=dim.h;
    ctx.globalCompositeOperation='source-over';
    ctx.clearRect(0,0,w,h);
    var grad=ctx.createLinearGradient(0,0,w,h);
    grad.addColorStop(0,'#dbe3ec');
    grad.addColorStop(0.42,'#f2f5f9');
    grad.addColorStop(0.58,'#ccd6e2');
    grad.addColorStop(1,'#b9c5d4');
    ctx.fillStyle=grad;
    ctx.fillRect(0,0,w,h);
    ctx.save();
    ctx.globalAlpha=0.45;
    ctx.strokeStyle='#ffffff';
    ctx.lineWidth=2;
    for(var x=-h;x<w+h;x+=18){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x+h,h);ctx.stroke();}
    ctx.restore();
    ctx.save();
    ctx.translate(w/2,h/2);
    ctx.rotate(-0.16);
    ctx.textAlign='center';
    ctx.textBaseline='middle';
    ctx.fillStyle='rgba(51,65,85,0.55)';
    ctx.font='800 26px -apple-system,"Segoe UI",sans-serif';
    ctx.fillText('刮 一 刮',0,-10);
    ctx.fillStyle='rgba(51,65,85,0.42)';
    ctx.font='700 13px -apple-system,"Segoe UI",sans-serif';
    ctx.fillText('看本期单双 · 大小',0,22);
    ctx.restore();
  }

  function resetCover(){
    revealed=false;
    drawing=false;
    lastPoint=null;
    moves=0;
    if(autoTimer){clearTimeout(autoTimer);autoTimer=null;}
    canvas.classList.remove('sc-done');
    canvas.style.pointerEvents='';
    if(ctx){
      paintCover();
      window.requestAnimationFrame(function(){if(!revealed&&ctx)paintCover();});
      hint.innerHTML='捏住鼠标 / 手指在上面的灰卡上刮一刮，看本期的 <b>单双</b> 和 <b>大小</b>';
      autoTimer=setTimeout(function(){if(!revealed)reveal(true);},60000);
    }else{
      hint.innerHTML='当前浏览器不支持刮刮乐，已直接展示本期 <b>单双</b> 和 <b>大小</b>';
    }
  }

  function coverCleared(){
    if(!ctx)return 1;
    var w=canvas.width,h=canvas.height;
    if(!w||!h)return 1;
    var data;
    try{data=ctx.getImageData(0,0,w,h).data;}catch(error){return 1;}
    var sampled=0,cleared=0;
    for(var i=3;i<data.length;i+=4*16){sampled++;if(data[i]<24)cleared++;}
    return sampled?cleared/sampled:1;
  }

  function reveal(force){
    if(revealed)return;
    if(!force&&coverCleared()<0.5)return;
    revealed=true;
    if(autoTimer){clearTimeout(autoTimer);autoTimer=null;}
    canvas.classList.add('sc-done');
    canvas.style.pointerEvents='none';
    hint.innerHTML='已刮开 <em>✅</em> 下一期开奖后会自动换一张新卡';
  }

  function pointOf(event){
    var rect=canvas.getBoundingClientRect();
    return {x:event.clientX-rect.left,y:event.clientY-rect.top};
  }
  function erase(from,to){
    if(!ctx)return;
    ctx.globalCompositeOperation='destination-out';
    ctx.lineCap='round';
    ctx.lineJoin='round';
    ctx.lineWidth=40;
    ctx.beginPath();
    ctx.moveTo(from.x,from.y);
    ctx.lineTo(to.x,to.y);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(to.x,to.y,20,0,Math.PI*2);
    ctx.fill();
  }
  function endDraw(){
    if(!drawing)return;
    drawing=false;
    lastPoint=null;
    reveal(false);
  }

  canvas.addEventListener('pointerdown',function(event){
    if(revealed)return;
    if(event.pointerType==='mouse'&&event.button!==0)return;
    event.preventDefault();
    try{canvas.setPointerCapture(event.pointerId);}catch(error){}
    drawing=true;
    lastPoint=pointOf(event);
    moves=0;
    erase(lastPoint,lastPoint);
  });
  canvas.addEventListener('pointermove',function(event){
    if(!drawing||revealed)return;
    event.preventDefault();
    var point=pointOf(event);
    if(lastPoint)erase(lastPoint,point);else erase(point,point);
    lastPoint=point;
    moves++;
    if(moves%10===0)reveal(false);
  });
  canvas.addEventListener('pointerup',endDraw);
  canvas.addEventListener('pointercancel',endDraw);
  allBtn.addEventListener('click',function(event){event.preventDefault();reveal(true);});

  function currentKey(){return text('disp_period')+'|'+digits().join('')+'|'+text('disp_hewei');}
  function sync(){
    var next=currentKey();
    if(next===key)return;
    var changed=!!key;
    key=next;
    render();
    if(changed){
      card.classList.remove('sc-new');
      void card.offsetWidth;
      card.classList.add('sc-new');
      if(pulseTimer)clearTimeout(pulseTimer);
      pulseTimer=setTimeout(function(){card.classList.remove('sc-new');},2800);
    }
  }

  window.addEventListener('resize',function(){
    if(resizeTimer)clearTimeout(resizeTimer);
    resizeTimer=setTimeout(function(){if(!revealed)paintCover();},180);
  });
  document.addEventListener('penta5:panel-updated',sync);
  if(window.MutationObserver){
    try{
      var observer=new MutationObserver(function(){
        if(observer.timer)clearTimeout(observer.timer);
        observer.timer=setTimeout(sync,120);
      });
      ['disp_period','disp_numbers','disp_hewei','disp_time'].forEach(function(id){
        var node=document.getElementById(id);
        if(node)observer.observe(node,{childList:true,characterData:true,subtree:true});
      });
    }catch(error){}
  }
  setInterval(sync,1500);

  window[INSTANCE_KEY]={sync:sync,reveal:function(){reveal(true);}};
  sync();
})();
</script>
'''


def main():
    if not PANEL.is_file():
        print('missing panel.html', file=sys.stderr)
        return 2
    html = PANEL.read_text(encoding='utf-8')
    if 'id="penta5-scratch-card"' in html or 'id="scratch-card"' in html:
        print('already patched, skip')
        return 0
    assert html.count('</head>') == 1
    assert html.count('</body>') == 1
    section_anchor = '    <!-- 3个核心 API 模型内容 -->'
    assert html.count(section_anchor) == 1
    html = html.replace('</head>', STYLE + '</head>', 1)
    html = html.replace(section_anchor, HTML + '\n' + section_anchor, 1)
    html = html.replace('</body>', SCRIPT + '</body>', 1)
    for token in ('id="scratch-card"', 'id="sc_board"', 'id="sc_shapes"', 'id="sc_canvas"',
                  'id="sc_all"', 'id="penta5-scratch-style"', 'id="penta5-scratch-card"'):
        assert html.count(token) == 1, (token, html.count(token))
    tmp = PANEL.with_suffix('.html.scratch.tmp')
    tmp.write_text(html, encoding='utf-8', newline='')
    os.replace(tmp, PANEL)
    print('patched panel.html bytes=%d' % len(html))
    return 0


if __name__ == '__main__':
    sys.exit(main())
