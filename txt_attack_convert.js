/* Penta5 attack-text -> TXT converter (shared, browser). Extracted from panel.html shot pipeline. */
(function(global){
"use strict";
var BROTHER_NUMBER_PAIRS=["01","12","23","34","45","56","67","78","89","09"];
function txtFormatTokenList(tokens,limit){
  tokens=Array.isArray(tokens)?tokens:[];
  tokens=tokens.map(function(token){return String(token==null?"":token).replace(/^各+/,"").replace(/各+$/,"").trim();}).filter(Boolean);
  if(limit!=null)tokens=tokens.slice(0,Math.max(0,Number(limit||0)));
  return tokens.length?tokens.join(" "):"";
}
function txtTodayKey(){
  var d=new Date();
  function p(n){return n<10?"0"+n:String(n);}
  return d.getFullYear()+p(d.getMonth()+1)+p(d.getDate());
}
function txtShotHasRetiredFourCodeToken(text){
  return /除(?:旧码|重复码|历史码)(?:300|500|800|1000|1300|最大|全部|MAX)/i.test(String(text||""));
}

function txtRandomDigitFileName(len){
  len=Math.max(6,Math.min(12,Number(len||8)||8));
  var out="";
  for(var i=0;i<len;i++)out+=String(Math.floor(Math.random()*10));
  if(out.charAt(0)==="0")out="1"+out.slice(1);
  return out+".txt";
}

function txtFileNameByCount(width,day,count){
  return txtRandomDigitFileName(8);
}

function txtFixedHeweiBaseText(){
  return "千1234567890百1234567890十1234567890个1234567890四定";
}

function txtShotNormalize(text){
  return String(text||"").replace(/[，,。；;\s\r\n\t]/g,"");
}

function txtNormalizeRange(a,b){
  var left=Number(a),right=Number(b);
  if(!isFinite(left)||!isFinite(right))return "";
  var start=Math.max(0,Math.min(36,Math.min(left,right)));
  var end=Math.max(0,Math.min(36,Math.max(left,right)));
  return String(start)+"-"+String(end);
}

function txtRangeObject(value){
  var nums=String(value||"").match(/\d+/g);
  if(!nums||nums.length<2)return null;
  var code=txtNormalizeRange(nums[0],nums[1]);
  if(!code)return null;
  var parts=code.split("-").map(Number);
  return {start:parts[0],end:parts[1],code:code};
}

function txtShotSegment(text,start,ends){
  text=String(text||"");
  var begin=txtShotSegmentStartIndex(text,start);
  if(begin<0)return "";
  begin+=String(start).length;
  var stop=text.length;
  (ends||[]).forEach(function(end){
    var idx=text.indexOf(end,begin);
    if(idx>=0&&idx<stop)stop=idx;
  });
  return text.slice(begin,stop);
}

function txtShotSegmentStartIndex(text,start){
  var raw=String(text||"");
  var label=String(start||"");
  // 千百十合 是 千百十个合 的前缀；百十个合 又嵌在 千百十个合 内，必须整词边界匹配
  if(label==="千百十合"){
    var heweiIdx=-1;
    while((heweiIdx=raw.indexOf(label,heweiIdx+1))>=0){
      if(raw.charAt(heweiIdx+4)!=="个")return heweiIdx;
    }
    return -1;
  }
  if(label==="千百合"){
    // 别名：仅匹配字面「千百合」，不吃「千百十合 / 千百个合」
    var aliasIdx=-1;
    while((aliasIdx=raw.indexOf(label,aliasIdx+1))>=0){
      var after=raw.charAt(aliasIdx+3);
      if(after==="十"||after==="个")continue;
      return aliasIdx;
    }
    return -1;
  }
  if(label==="千百个合"){
    // 不与「千百十个合」混淆：千百个合 第三字是「个」而非「十」
    return raw.indexOf(label);
  }
  if(label==="百十个合"){
    var idx=-1;
    while((idx=raw.indexOf(label,idx+1))>=0){
      if(raw.charAt(idx-1)!=="千")return idx;
    }
    return -1;
  }
  return raw.indexOf(label);
}
function txtShotOptionalDigitsFirst(text,labels,ends){
  var list=Array.isArray(labels)?labels:[labels];
  for(var i=0;i<list.length;i++){
    var raw=txtShotSegment(text,list[i],ends);
    if(raw){
      var digits=txtShotDigits(raw);
      return digits.length?digits:"0123456789".split("");
    }
  }
  return "0123456789".split("");
}

function txtShotDigits(value){
  var seen={},out=[];
  String(value||"").replace(/\d/g,function(digit){
    if(!seen[digit]){
      seen[digit]=true;
      out.push(digit);
    }
    return digit;
  });
  return out;
}

function txtShotRequiredDigits(text,label,ends){
  return txtShotDigits(txtShotSegment(text,label,ends));
}

function txtShotOptionalDigits(text,label,ends){
  var raw=txtShotSegment(text,label,ends);
  if(!raw)return "0123456789".split("");
  var digits=txtShotDigits(raw);
  return digits.length?digits:"0123456789".split("");
}

function txtRemoveDigitFromList(list,digit){
  digit=String(digit);
  var source=Array.isArray(list)?list.slice():[];
  var next=source.filter(function(item){return String(item)!==digit;});
  return next.length?next:source;
}

function txtRemoveExcludedDigitFromList(list,digit){
  digit=String(digit);
  var source=Array.isArray(list)?list.slice():[];
  if(source.length===1&&String(source[0])===digit){
    source="0123456789".split("");
  }
  var next=source.filter(function(item){return String(item)!==digit;});
  return next.length?next:source;
}

function txtApplyShortShotExclusions(raw,rule){
  var text=String(raw||"");
  var match;
  var positionTargets={
    "千":"qian","千位":"qian",
    "百":"bai","百位":"bai",
    "十":"shi","十位":"shi",
    "个":"ge","个位":"ge"
  };
  var positionRegex=/除(千位|百位|十位|个位|千|百|十|个)([0-9])/g;
  while((match=positionRegex.exec(text))){
    var key=positionTargets[match[1]];
    if(key&&rule[key])rule[key]=txtRemoveExcludedDigitFromList(rule[key],match[2]);
  }
  var heweiRules=[
    {regex:/除(?:千百十个|四位|四定|四合|总合)(?:合|尾)?([0-9])/g,key:"qianBaiShiGeHewei"},
    {regex:/除(?:千百个)(?:合|尾)?([0-9])/g,key:"qianBaiGeHewei"},
    {regex:/除(?:千百十|千百合|前三|前3)(?:合|尾)?([0-9])/g,key:"qianBaiShiHewei"},
    {regex:/除(?:百十个|百十合|后三|后3)(?:合|尾)?([0-9])/g,key:"baiShiGeHewei"}
  ];
  heweiRules.forEach(function(cfg){
    cfg.regex.lastIndex=0;
    while((match=cfg.regex.exec(text))){
      if(rule[cfg.key])rule[cfg.key]=txtRemoveExcludedDigitFromList(rule[cfg.key],match[1]);
    }
  });
  return rule;
}

function txtShotExcludeShapes(text){
  var out=[],match;
  // 四定形态4字；三定千百十形态3字（除大大小 / 除大大大）
  var regex4=/除([大小单双]{4})/g;
  while((match=regex4.exec(text))){
    if(out.indexOf(match[1])<0)out.push(match[1]);
  }
  var regex3=/除([大小单双]{3})(?![大小单双])/g;
  while((match=regex3.exec(text))){
    if(out.indexOf(match[1])<0)out.push(match[1]);
  }
  return out;
}

function txtShotExcludePairs(text){
  var out=[],match;
  // 认：除对数16-19 / 对数16-19 / 除对数16.19 / 除对数16、19；不误吃「含对数/中对数」
  var regex=/(?:除对数|对数)(\d{2,3}(?:[-.、~/]\d{2,3})*)/g;
  var raw=String(text||"");
  while((match=regex.exec(raw))){
    if(match[0].indexOf("除对数")!==0){
      var prev=match.index>0?raw.charAt(match.index-1):"";
      if(prev==="含"||prev==="中")continue;
    }
    String(match[1]||"").split(/[-.、~/]/).forEach(function(part){
      var digits=String(part||"").replace(/\D/g,"");
      if(digits.length>=2&&out.indexOf(digits)<0)out.push(digits);
    });
  }
  return out;
}

/** 合尾「除百十个合6」→保留码「百十个合012345789」；裸「除05」→「除对数05」（定位池缩短口径，不改算法选型） */
function txtCanonicalizeAttackHeweiAndDuishu(raw){
  var text=String(raw==null?"":raw).replace(/\s+/g,"");
  if(!text)return text;
  text=text.replace(/除(千百十合|百十个合|千百个合|千十个合|千百十个合|万十个合)(\d)/g,function(_m,label,digit){
    return label+("1234567890".split(String(digit)).join(""));
  });
  text=text.replace(/除(\d{2})(?!\d)/g,function(m,digits,offset){
    var before=text.slice(Math.max(0,offset-6),offset);
    if(/对数$/.test(before)||/跨度$/.test(before)||/和值$/.test(before)||/(?:^|[^和])值$/.test(before))return m;
    if(/合$/.test(before))return m;
    return "除对数"+digits;
  });
  return text;
}

/**
 * 攻击语对数强制对齐面板「开奖号码下 · 三对数排除 · 本期排除」。
 * pair 必须是两位数字；只改除对数片段，不动定位/合尾/二数合选型。
 */
function txtApplyPanelThreeDuishuPair(raw,pair){
  var text=txtCanonicalizeAttackHeweiAndDuishu(raw);
  var selected=String(pair==null?"":pair).replace(/\D/g,"").slice(0,2);
  if(!/^\d{2}$/.test(selected))return text;
  var hadTail=/各$/.test(text);
  if(hadTail)text=text.replace(/各+$/,"");
  // 清掉旧除对数/对数（保留含对数/中对数）
  text=text.replace(/含对数\d{2,3}(?:[-.、~/]\d{2,3})*/g,"\u0001$&\u0001");
  text=text.replace(/中对数\d{2,3}(?:[-.、~/]\d{2,3})*/g,"\u0002$&\u0002");
  text=text.replace(/(?:除对数|对数)\d{2,3}(?:[-.、~/]\d{2,3})*/g,"");
  text=text.replace(/\u0001/g,"").replace(/\u0002/g,"");
  text=text+"除对数"+selected;
  if(hadTail||/三定|四定/.test(text))text=text.replace(/各*$/,"")+"各";
  return text;
}

function txtShotExcludeSums(text){
  var out=[],match,regex=/除(?:和值|值)(\d+)(?![\d到至-])/g;
  while((match=regex.exec(String(text||"")))){
    var value=Number(match[1]);
    if(Number.isFinite(value)&&out.indexOf(value)<0)out.push(value);
  }
  return out;
}

function txtShotExcludeSumRanges(text){
  var out=[],match,regex=/除(?:和值|值)(\d+)(?:到|至|-)(\d+)/g;
  while((match=regex.exec(String(text||"")))){
    var code=txtNormalizeRange(match[1],match[2]);
    if(code&&out.indexOf(code)<0)out.push(code);
  }
  return out;
}

function txtShotExcludeSpans(text){
  var out=[],match,regex=/除(?:跨度|跨)([0-9])/g;
  while((match=regex.exec(String(text||"")))){
    if(out.indexOf(match[1])<0)out.push(match[1]);
  }
  return out;
}

function txtShotExcludeRepeatShapes(text){
  var out=[],match,regex=/除(重号|一对|双重|三重|四重)/g;
  while((match=regex.exec(String(text||"")))){
    if(out.indexOf(match[1])<0)out.push(match[1]);
  }
  return out;
}

function txtShotExcludeConsecutiveShapes(text){
  var out=[],match,regex=/除(连号|二连|三连|四连)/g;
  while((match=regex.exec(String(text||"")))){
    if(out.indexOf(match[1])<0)out.push(match[1]);
  }
  return out;
}

function txtShotExcludeParityRatios(text){
  var out=[],match,regex=/除(?:奇偶比)?([0-4]单[0-4]双)/g;
  while((match=regex.exec(String(text||"")))){
    if(out.indexOf(match[1])<0)out.push(match[1]);
  }
  return out;
}

function txtShotExcludeSizeRatios(text){
  var out=[],match,regex=/除(?:大小比)?([0-4]大[0-4]小)/g;
  while((match=regex.exec(String(text||"")))){
    if(out.indexOf(match[1])<0)out.push(match[1]);
  }
  return out;
}

function txtShotHitPairs(text){
  var out=[],match,regex=/(?:含对数|中对数)(\d{2,3}(?:[-.、~/]\d{2,3})*)/g;
  while((match=regex.exec(String(text||"")))){
    String(match[1]||"").split(/[-.、~/]/).forEach(function(part){
      var digits=String(part||"").replace(/\D/g,"");
      if(digits.length>=2&&out.indexOf(digits)<0)out.push(digits);
    });
  }
  return out;
}

function txtShotLooksLikeFullModel(text){
  var raw=txtShotNormalize(text);
  return raw.indexOf("千")>=0&&raw.indexOf("百")>=0&&raw.indexOf("十")>=0&&raw.indexOf("个")>=0&&raw.indexOf("四定")>=0;
}

function txtShotLooksLikeShortExclusionCommand(text){
  var raw=txtShotNormalize(text);
  if(!raw||txtShotLooksLikeFullModel(raw))return false;
  if(/(?:排除|避开|杀|除)/.test(raw))return true;
  if(/(?:二数合|三数合|和值|跨度|对数)/.test(raw))return true;
  return false;
}

function txtShotRuleFromText(text,options){
  var raw=txtShotNormalize(text);
  if(txtShotHasRetiredFourCodeToken(raw))return {error:"完整四码旧码回删已下线，请删除该指令后重试。"};
  if(!txtShotLooksLikeFullModel(raw))return null;
  var opts=options&&typeof options==="object"?options:{};
  var tailEnds=["千百十二数合","二数合","三数合","取二兄弟","兄弟号","除","含","中","各"];
  var heweiEnds=["千百十合","千百合","千百个合","千十个合","万十个合","百十个合","千百十个合"].concat(tailEnds);
  var rule={
    raw:raw,
    qian:txtShotRequiredDigits(raw,"千",["百"]),
    bai:txtShotRequiredDigits(raw,"百",["十"]),
    shi:txtShotRequiredDigits(raw,"十",["个"]),
    ge:txtShotRequiredDigits(raw,"个",["四定"].concat(heweiEnds)),
    // 千百十合；兼容别名「千百合」
    qianBaiShiHewei:txtShotOptionalDigitsFirst(raw,["千百十合","千百合"],["千百个合","千十个合","万十个合","百十个合","千百十个合"].concat(tailEnds)),
    // 千百个合：千+百+个（跳过十位）
    qianBaiGeHewei:txtShotOptionalDigits(raw,"千百个合",["千十个合","万十个合","百十个合","千百十个合"].concat(tailEnds)),
    // 千十个合 / 万十个合：千+十+个（跳过百位）
    wanBaiShiHewei:txtShotOptionalDigitsFirst(raw,["千十个合","万十个合"],["百十个合","千百十个合"].concat(tailEnds)),
    baiShiGeHewei:txtShotOptionalDigits(raw,"百十个合",["千百十个合"].concat(tailEnds)),
    qianBaiShiGeHewei:txtShotOptionalDigits(raw,"千百十个合",tailEnds),
    twoHeweiHit:(function(){
      var qianBaiShi=txtShotHitHeweiDigits(raw,"千百十二数合");
      return qianBaiShi.length?qianBaiShi:txtShotHitHeweiDigits(raw,"二数合");
    })(),
    threeHeweiHit:txtShotHitHeweiDigits(raw,"三数合"),
    twoHeweiScopeIndexes:/千百十二数合\d+/.test(raw)?[0,1,2]:(Array.isArray(opts.twoHeweiScopeIndexes)?opts.twoHeweiScopeIndexes.slice():undefined),
    brotherChoice:txtShotBrotherChoice(raw),
    shapes:txtShotExcludeShapes(raw),
    pairs:txtShotExcludePairs(raw),
    sumAvoid:txtShotExcludeSums(raw),
    sumRangeAvoid:txtShotExcludeSumRanges(raw),
    spanAvoid:txtShotExcludeSpans(raw),
    repeatAvoid:txtShotExcludeRepeatShapes(raw),
    consecutiveAvoid:txtShotExcludeConsecutiveShapes(raw),
    parityRatioAvoid:txtShotExcludeParityRatios(raw),
    sizeRatioAvoid:txtShotExcludeSizeRatios(raw),
    hitPairs:txtShotHitPairs(raw)
  };
  txtApplyShortShotExclusions(raw,rule);
  var missing=[];
  [["千",rule.qian],["百",rule.bai],["十",rule.shi],["个",rule.ge]].forEach(function(item){
    if(!item[1].length)missing.push(item[0]);
  });
  if(missing.length)return {error:"完整射击文字缺少 "+missing.join("、")+" 位数字。"};
  return rule;
}

function txtShotContains(list,digit){
  return (list||[]).indexOf(String(digit))>=0;
}

function txtShotHitHeweiDigits(text,label){
  var regex=new RegExp(label+"(\\d+)");
  var match=regex.exec(String(text||""));
  return match?txtShotDigits(match[1]):[];
}

function txtShotHewei(){
  var total=0;
  for(var i=0;i<arguments.length;i++)total+=Number(arguments[i]||0);
  return String(total%10);
}

function txtShotAnyTwoHeweiHit(code,selected,scopeIndexes){
  if(!selected||!selected.length)return true;
  var indexes=Array.isArray(scopeIndexes)&&scopeIndexes.length>=2
    ? scopeIndexes.map(Number).filter(function(value){return Number.isInteger(value)&&value>=0&&value<=3;})
    : [0,1,2,3];
  var uniq=[],seen={};
  indexes.forEach(function(index){
    if(seen[index])return;
    seen[index]=true;
    uniq.push(index);
  });
  if(uniq.length<2)uniq=[0,1,2,3];
  for(var i=0;i<uniq.length;i++){
    for(var j=i+1;j<uniq.length;j++){
      if(txtShotContains(selected,txtShotHewei(code.charAt(uniq[i]),code.charAt(uniq[j]))))return true;
    }
  }
  return false;
}

function txtShotAnyThreeHeweiHit(code,selected){
  if(!selected||!selected.length)return true;
  var triples=[[0,1,2],[0,1,3],[0,2,3],[1,2,3]];
  return triples.some(function(item){
    return txtShotContains(selected,txtShotHewei(code.charAt(item[0]),code.charAt(item[1]),code.charAt(item[2])));
  });
}

function txtShotDigitShape(digit,token){
  var value=Number(digit||0);
  if(token==="单"||token==="双")return value%2?"单":"双";
  if(token==="大"||token==="小")return value>=5?"大":"小";
  return "";
}

function txtShotCodeMatchesShape(code,shape){
  code=String(code||"");
  shape=String(shape||"");
  if(shape.length===3){
    if(code.length<3)return false;
    for(var i=0;i<3;i++){
      if(txtShotDigitShape(code.charAt(i),shape.charAt(i))!==shape.charAt(i))return false;
    }
    return true;
  }
  if(code.length!==4||shape.length!==4)return false;
  for(var j=0;j<4;j++){
    if(txtShotDigitShape(code.charAt(j),shape.charAt(j))!==shape.charAt(j))return false;
  }
  return true;
}

function txtShotCodeContainsPair(code,pair){
  code=String(code||"");
  return String(pair||"").split("").every(function(digit){return code.indexOf(digit)>=0;});
}

function txtShotBrotherPairs(){
  return Array.isArray(typeof BROTHER_NUMBER_PAIRS==="undefined"?null:BROTHER_NUMBER_PAIRS)?BROTHER_NUMBER_PAIRS:["01","12","23","34","45","56","67","78","89","09"];
}

function txtShotCodeHasBrother(code){
  code=String(code||"").replace(/\D/g,"").slice(0,4);
  if(code.length<4)return false;
  return txtShotBrotherPairs().some(function(pair){return txtShotCodeContainsPair(code,pair);});
}

function txtShotBrotherChoice(text){
  var match=/(?:取二兄弟|兄弟号)(有|否)?/.exec(String(text||""));
  return match?(match[1]||"有"):"";
}

function txtShotCodeSum(code){
  var total=0;
  String(code||"").split("").forEach(function(digit){total+=Number(digit||0);});
  return total;
}

function txtShotCodeSpan(code){
  var nums=String(code||"").split("").map(Number).filter(function(n){return isFinite(n);});
  if(nums.length!==4)return null;
  return Math.max.apply(null,nums)-Math.min.apply(null,nums);
}

function txtShotCodeRepeatShape(code){
  var chars=String(code||"").split("");
  if(chars.length!==4)return "";
  var counts={};
  chars.forEach(function(ch){counts[ch]=Number(counts[ch]||0)+1;});
  var values=Object.keys(counts).map(function(key){return counts[key];}).sort(function(a,b){return b-a;});
  if(!values.length)return "";
  if(values[0]>=4)return "四重";
  if(values[0]>=3)return "三重";
  var pairs=values.filter(function(v){return v===2;}).length;
  if(pairs>=2)return "双重";
  if(pairs===1)return "一对";
  return "无重";
}

function txtShotCodeHasRepeat(code){
  var shape=txtShotCodeRepeatShape(code);
  return shape&&shape!=="无重";
}

function txtShotCodeMaxConsecutiveRun(code){
  var nums=String(code||"").split("").map(Number).filter(function(n){return isFinite(n);});
  if(nums.length!==4)return 0;
  var seen={},unique=[];
  nums.forEach(function(n){if(!seen[n]){seen[n]=true;unique.push(n);}});
  unique.sort(function(a,b){return a-b;});
  var best=unique.length?1:0,current=unique.length?1:0;
  for(var i=1;i<unique.length;i++){
    if(unique[i]===unique[i-1]+1)current++;
    else current=1;
    if(current>best)best=current;
  }
  return best;
}

function txtShotCodeConsecutiveShape(code){
  var run=txtShotCodeMaxConsecutiveRun(code);
  if(run>=4)return "四连";
  if(run>=3)return "三连";
  if(run>=2)return "二连";
  return "无连";
}

function txtShotCodeHasConsecutive(code){
  return txtShotCodeMaxConsecutiveRun(code)>=2;
}

function txtShotCodeParityRatio(code){
  var nums=String(code||"").split("").map(Number).filter(function(n){return isFinite(n);});
  if(nums.length!==4)return "";
  var odd=nums.filter(function(n){return Math.abs(n)%2===1;}).length;
  return String(odd)+"单"+String(4-odd)+"双";
}

function txtShotCodeSizeRatio(code){
  var nums=String(code||"").split("").map(Number).filter(function(n){return isFinite(n);});
  if(nums.length!==4)return "";
  var big=nums.filter(function(n){return n>=5;}).length;
  return String(big)+"大"+String(4-big)+"小";
}

function txtShotPassesRule(code,rule){
  var a=code.charAt(0),b=code.charAt(1),c=code.charAt(2),d=code.charAt(3);
  if(!txtShotContains(rule.qian,a))return false;
  if(!txtShotContains(rule.bai,b))return false;
  if(!txtShotContains(rule.shi,c))return false;
  if(!txtShotContains(rule.ge,d))return false;
  if(!txtShotContains(rule.qianBaiShiHewei,txtShotHewei(a,b,c)))return false;
  if(!txtShotContains(rule.qianBaiGeHewei||"0123456789".split(""),txtShotHewei(a,b,d)))return false;
  if(!txtShotContains(rule.baiShiGeHewei,txtShotHewei(b,c,d)))return false;
  if(!txtShotContains(rule.qianBaiShiGeHewei,txtShotHewei(a,b,c,d)))return false;
  if(!txtShotAnyTwoHeweiHit(code,rule.twoHeweiHit,rule.twoHeweiScopeIndexes))return false;
  if(!txtShotAnyThreeHeweiHit(code,rule.threeHeweiHit))return false;
  if(rule.brotherChoice==="有"&&!txtShotCodeHasBrother(code))return false;
  if(rule.brotherChoice==="否"&&txtShotCodeHasBrother(code))return false;
  for(var i=0;i<rule.shapes.length;i++){
    if(txtShotCodeMatchesShape(code,rule.shapes[i]))return false;
  }
  for(var s=0;s<rule.sumAvoid.length;s++){
    if(txtShotCodeSum(code)===Number(rule.sumAvoid[s]))return false;
  }
  for(var sr=0;sr<rule.sumRangeAvoid.length;sr++){
    var range=txtRangeObject(rule.sumRangeAvoid[sr]);
    var sum=txtShotCodeSum(code);
    if(range&&sum>=range.start&&sum<=range.end)return false;
  }
  for(var sp=0;sp<rule.spanAvoid.length;sp++){
    if(String(txtShotCodeSpan(code))===String(rule.spanAvoid[sp]))return false;
  }
  for(var rep=0;rep<rule.repeatAvoid.length;rep++){
    var repeatRule=String(rule.repeatAvoid[rep]);
    if((repeatRule==="重号"&&txtShotCodeHasRepeat(code))||txtShotCodeRepeatShape(code)===repeatRule)return false;
  }
  for(var con=0;con<rule.consecutiveAvoid.length;con++){
    var consecutiveRule=String(rule.consecutiveAvoid[con]);
    if((consecutiveRule==="连号"&&txtShotCodeHasConsecutive(code))||txtShotCodeConsecutiveShape(code)===consecutiveRule)return false;
  }
  for(var pr=0;pr<rule.parityRatioAvoid.length;pr++){
    if(txtShotCodeParityRatio(code)===String(rule.parityRatioAvoid[pr]))return false;
  }
  for(var sz=0;sz<rule.sizeRatioAvoid.length;sz++){
    if(txtShotCodeSizeRatio(code)===String(rule.sizeRatioAvoid[sz]))return false;
  }
  for(var j=0;j<rule.pairs.length;j++){
    if(txtShotCodeContainsPair(code,rule.pairs[j]))return false;
  }
  for(var h=0;h<rule.hitPairs.length;h++){
    if(!txtShotCodeContainsPair(code,rule.hitPairs[h]))return false;
  }
  return true;
}

function txtShotGenerateCodes(rule){
  var out=[];
  for(var i=0;i<10000;i++){
    var code=("0000"+i).slice(-4);
    if(txtShotPassesRule(code,rule))out.push(code);
  }
  return out;
}

function txtShotRuleSummary(rule){
  return "千"+rule.qian.join("")
    +" 百"+rule.bai.join("")
    +" 十"+rule.shi.join("")
    +" 个"+rule.ge.join("")
    +" 千百十合"+rule.qianBaiShiHewei.join("")
    +((rule.qianBaiGeHewei||[]).join("")&&rule.qianBaiGeHewei.join("")!=="0123456789"?" 千百个合"+rule.qianBaiGeHewei.join(""):"")
    +" 百十个合"+rule.baiShiGeHewei.join("")
    +(rule.qianBaiShiGeHewei.join("")!=="0123456789"?" 千百十个合"+rule.qianBaiShiGeHewei.join(""):"")
    +(rule.twoHeweiHit.length?((Array.isArray(rule.twoHeweiScopeIndexes)&&rule.twoHeweiScopeIndexes.join(",")==="0,1,2")?" 千百十二数合":" 二数合")+rule.twoHeweiHit.join(""):"")
    +(rule.threeHeweiHit.length?" 三数合"+rule.threeHeweiHit.join(""):"")
    +(rule.brotherChoice?" 取二兄弟":"")
    +(rule.shapes.length?" 除"+rule.shapes.join("、除"):"")
    +(rule.sumAvoid.length?" 除和值"+rule.sumAvoid.join("、除和值"):"")
    +(rule.sumRangeAvoid.length?" 除和值"+rule.sumRangeAvoid.join("、除和值"):"")
    +(rule.spanAvoid.length?" 除跨度"+rule.spanAvoid.join("、除跨度"):"")
    +(rule.repeatAvoid.length?" 除"+rule.repeatAvoid.join("、除"):"")
    +(rule.consecutiveAvoid.length?" 除"+rule.consecutiveAvoid.join("、除"):"")
    +(rule.parityRatioAvoid.length?" 除奇偶比"+rule.parityRatioAvoid.join("、除奇偶比"):"")
    +(rule.sizeRatioAvoid.length?" 除大小比"+rule.sizeRatioAvoid.join("、除大小比"):"")
    +(rule.pairs.length?" 除对数"+rule.pairs.join("-"):"")
    +(rule.hitPairs.length?" 含对数"+rule.hitPairs.join("-"):"");
}

function txtShotResultFromInput(raw,day,options){
  var rule=txtShotRuleFromText(raw,options);
  if(!rule)return null;
  if(rule.error)return {ok:false,message:rule.error};
  var codes=txtShotGenerateCodes(rule);
  return {
    ok:true,
    width:4,
    count:codes.length,
    text:txtFormatTokenList(codes),
    preview:txtFormatTokenList(codes,120),
    fileName:txtFileNameByCount(4,day,codes.length),
    source:"shot-text",
    label:"射击文字四定TXT",
    note:txtShotRuleSummary(rule)
  };
}

function txtShortShotResultFromInput(raw,day){
  if(!txtShotLooksLikeShortExclusionCommand(raw))return null;
  var command=String(raw||"").replace(/^[\s\r\n\t，,。；;、各]+/g,"").trim();
  if(!command)return null;
  var base=txtFixedHeweiBaseText().replace(/各+$/,"");
  var modelText=(base+command).replace(/\s+/g,"").replace(/各+$/,"");
  var result=txtShotResultFromInput(modelText,day);
  if(!result)return null;
  if(result.ok){
    result.source="short-shot-text";
    result.label="四定TXT";
    result.modelText=modelText;
    result.note="按基础四定转换："+txtShotRuleSummary(txtShotRuleFromText(modelText));
  }
  return result;
}

function convertAttackText(raw,options){
  options=options&&typeof options==="object"?options:{};
  var day=options.day||txtTodayKey();
  var text=txtCanonicalizeAttackHeweiAndDuishu(String(raw==null?"":raw));
  if(options.panelThreeDuishuPair)text=txtApplyPanelThreeDuishuPair(text,options.panelThreeDuishuPair);
  if(!text.trim())return {ok:false,message:"请粘贴攻击语后再转换。"};
  var shot=txtShotResultFromInput(text,day,options);
  if(shot)return shot;
  var shortShot=txtShortShotResultFromInput(text,day);
  if(shortShot)return shortShot;
  return {ok:false,message:"未识别为完整攻击语。请粘贴含「千…百…十…个…四定」的文本，或短指令如「除千1除对数79」。"};
}

var THREE_DING_FULL_GROUP_SIZE=1000;
var THREE_DING_DELETE_TARGET=200;
var THREE_DING_REMAIN_TARGET=800;
var THREE_DING_HISTORY_MIN=200;
/** 回删只扫阿里云最近 N 期（四种三定统一） */
var THREE_DING_HISTORY_SCAN_LIMIT=200;
// Compatibility aliases for existing callers. The target is fixed and cannot be overridden.
var THREE_DING_REMAIN_MIN=THREE_DING_REMAIN_TARGET;
var THREE_DING_REMAIN_MAX=THREE_DING_REMAIN_TARGET;
var THREE_DING_POS_KEYS=["qian","bai","shi","ge"];
var THREE_DING_POS_LABELS=["千","百","十","个"];
var THREE_DING_COMBOS=[
  {name:"千百十",positions:[0,1,2],blankIndex:3,aliases:["前三","前3"]},
  {name:"千百个",positions:[0,1,3],blankIndex:2,aliases:[]},
  {name:"千十个",positions:[0,2,3],blankIndex:1,aliases:["万十个"]},
  {name:"百十个",positions:[1,2,3],blankIndex:0,aliases:["后三","后3"]}
];

function txtThreeDingComboByName(name){
  var key=String(name||"");
  for(var i=0;i<THREE_DING_COMBOS.length;i++){
    var combo=THREE_DING_COMBOS[i];
    if(combo.name===key)return combo;
    if((combo.aliases||[]).indexOf(key)>=0)return combo;
  }
  return null;
}

function txtThreeDingComboByPositions(positions){
  var key=(positions||[]).slice().map(Number).join(",");
  for(var i=0;i<THREE_DING_COMBOS.length;i++){
    if(THREE_DING_COMBOS[i].positions.join(",")===key)return THREE_DING_COMBOS[i];
  }
  return null;
}

function txtLooksLikeThreeDingAttack(text){
  var raw=txtShotNormalize(text);
  if(!raw)return false;
  if(/三定|3定|三位|3位/.test(raw))return true;
  // 千百十齐全、无个位/四定 → 按三定处理
  if(raw.indexOf("千")>=0&&raw.indexOf("百")>=0&&(raw.indexOf("十")>=0||/\+\d/.test(raw))&&raw.indexOf("个")<0&&raw.indexOf("四定")<0)return true;
  // 百十个 / 万十个 / 千十个 / 千百个：任意三位齐全且无四定
  var prep=txtThreeDingPreprocess(raw);
  if(prep&&prep.found){
    var n=0;
    THREE_DING_POS_LABELS.forEach(function(lab){if(prep.found[lab]&&prep.found[lab].length)n++;});
    if(n>=3&&raw.indexOf("四定")<0)return true;
  }
  return false;
}

/** 预处理：万→千、百+十简写；只认「位标签+数字」，不吃合尾词里的千/百/十/个 */
function txtThreeDingPreprocess(raw){
  var text=txtShotNormalize(raw);
  text=text.replace(/万(?=\d)/g,"千");
  text=text.replace(/百(\d{1,10})\+(\d{1,10})/g,"百$1十$2");
  if(!/十\d/.test(text))text=text.replace(/\+(\d{1,10})/g,"十$1");
  var found={};
  var re=/(千|百|十|个)(\d+)/g,match;
  while((match=re.exec(text))){
    if(!found[match[1]])found[match[1]]=txtShotDigits(match[2]);
  }
  return {text:text,found:found};
}

function txtThreeDingResolveCombo(text,found){
  found=found||{};
  var has={};
  THREE_DING_POS_LABELS.forEach(function(lab){has[lab]=!!(found[lab]&&found[lab].length);});
  // 显式组合名（避开「百十个合」「千百十合」等合尾词）
  if(/(?:万十个|千十个)(?!合)/.test(text))return txtThreeDingComboByName("千十个");
  if(/(?:百十个)(?!合)|后三(?![合个十])/.test(text))return txtThreeDingComboByName("百十个");
  if(/(?:千百个)(?!合)/.test(text))return txtThreeDingComboByName("千百个");
  if(/(?:千百十)(?!个?合)|前三(?![合个十])/.test(text)&&!has["个"])return txtThreeDingComboByName("千百十");

  var present=[];
  THREE_DING_POS_LABELS.forEach(function(lab,idx){if(has[lab])present.push(idx);});
  if(present.length===3){
    var byPos=txtThreeDingComboByPositions(present);
    if(byPos)return byPos;
  }
  // 四位齐全（抓码器常出千…百…十…个…四定）：回删默认按百十个，禁止静默丢掉「个」改走千百十
  if(present.length===4)return txtThreeDingComboByName("百十个");
  if(has["百"]&&has["十"]&&has["个"]&&!has["千"])return txtThreeDingComboByName("百十个");
  if(has["千"]&&has["十"]&&has["个"]&&!has["百"])return txtThreeDingComboByName("千十个");
  if(has["千"]&&has["百"]&&has["个"]&&!has["十"])return txtThreeDingComboByName("千百个");
  if(has["千"]&&has["百"]&&has["十"])return txtThreeDingComboByName("千百十");
  return null;
}

function txtThreeDingExtractTail(text){
  text=String(text||"");
  var marks=["三定","3定","三位","3位","四定"];
  var idx=-1;
  marks.forEach(function(mark){
    var at=text.indexOf(mark);
    if(at>=0&&(idx<0||at<idx))idx=at;
  });
  if(idx>=0)return text.slice(idx);
  var heweiMarks=["千百十合","百十合","千百合","千百个合","千十个合","万十个合","百十个合","千百十个合","千百十二数合","二数合","三数合","合","除","含对数","中对数","对数"];
  heweiMarks.forEach(function(mark){
    var at=txtShotSegmentStartIndex(text,mark);
    if(at<0)at=text.indexOf(mark);
    if(at>=0&&(idx<0||at<idx))idx=at;
  });
  if(idx>=0)return text.slice(idx);
  var re=/(千|百|十|个)\d+/g,lastEnd=0,m;
  while((m=re.exec(text)))lastEnd=m.index+m[0].length;
  return text.slice(lastEnd);
}

/**
 * 把任意三位组合规范成四定底稿：空位补全码，三定→四定。
 * 返回 {ok, combo, normalized, found} 或 {error}
 */
function txtNormalizeThreeDingAttackText(raw){
  var prep=txtThreeDingPreprocess(txtCanonicalizeAttackHeweiAndDuishu(raw));
  var text=prep.text;
  var found=prep.found;
  var combo=txtThreeDingResolveCombo(text,found);
  if(!combo){
    return {error:"未识别为三定攻击语。支持千百十 / 百十个 / 千十个(万十个) / 千百个；空位输出 x。示例：百134…+123…个123…三定千百十合…除对数06"};
  }
  var missing=[];
  combo.positions.forEach(function(pos){
    var lab=THREE_DING_POS_LABELS[pos];
    if(!found[lab]||!found[lab].length)missing.push(lab);
  });
  if(missing.length){
    return {error:combo.name+"三定缺少 "+missing.join("、")+" 位数字。"};
  }
  var full="0123456789";
  var head=THREE_DING_POS_LABELS.map(function(lab){
    return lab+((found[lab]&&found[lab].length)?found[lab].join(""):full);
  }).join("");
  var tail=txtThreeDingExtractTail(text);
  if(/三定|3定|三位|3位/.test(tail)){
    tail=tail.replace(/三定|3定|三位|3位/,"四定");
  }else if(tail.indexOf("四定")<0){
    tail="四定"+tail;
  }
  // 三定旧短写「百十合」按当前三位合尾处理；存入百十个合字段供三定过滤复用。
  tail=tail.replace(/(^|[^千除])百十合(?=\d)/g,"$1百十个合");
  if(/(^|[^千])百十合(?=\d)/.test(tail)){
    return {error:"三定攻击语中的“百十合”位置不明确，已停止生成。"};
  }
  // 三定框架短写：合→千百十合（头已有千百十，展示不重复）；二数合保持短写即可
  tail=tail.replace(/四定合(\d+)/,"四定千百十合$1");
  return {
    ok:true,
    combo:combo,
    found:found,
    normalized:head+tail
  };
}

function txtThreeDingMaskToken(code3,combo,placeholder){
  code3=String(code3||"");
  placeholder=placeholder||"x";
  if(!/^\d{3}$/.test(code3)||!combo)return code3+placeholder;
  var out=[placeholder,placeholder,placeholder,placeholder];
  combo.positions.forEach(function(pos,i){out[pos]=code3.charAt(i);});
  out[combo.blankIndex]=placeholder;
  return out.join("");
}

function txtThreeDingUnmaskToken(token){
  var t=String(token==null?"":token).replace(/^各+/,"").replace(/各+$/,"").trim();
  if(/^\d{3}$/.test(t))return t;
  if(/^\d{3}[xX+]$/.test(t))return t.slice(0,3);
  if(/^[xX+\d]{4}$/.test(t)&&(t.match(/[xX+]/g)||[]).length===1){
    return t.replace(/[xX+]/g,"");
  }
  var digits=t.replace(/\D/g,"");
  return /^\d{3}$/.test(digits)?digits:"";
}

function txtFormatThreeDingGeList(tokens,limit,comboOrBlank){
  tokens=Array.isArray(tokens)?tokens:[];
  var combo=null;
  if(typeof comboOrBlank==="number"){
    if(comboOrBlank===3)combo=txtThreeDingComboByName("千百十");
    else if(comboOrBlank===0)combo=txtThreeDingComboByName("百十个");
    else if(comboOrBlank===1)combo=txtThreeDingComboByName("千十个");
    else if(comboOrBlank===2)combo=txtThreeDingComboByName("千百个");
  }else if(typeof comboOrBlank==="string"){
    // 桌面回删常传 comboName 字符串；以前忽略字符串会默认成千百十，千百个被画成 037x（错）
    combo=txtThreeDingComboByName(comboOrBlank);
  }else if(comboOrBlank&&typeof comboOrBlank==="object"){
    combo=comboOrBlank.name?comboOrBlank:txtThreeDingComboByName(comboOrBlank.comboName||comboOrBlank.name)
      ||(comboOrBlank.positions?txtThreeDingComboByPositions(comboOrBlank.positions):null);
  }
  if(!combo)combo=txtThreeDingComboByName("千百十");
  var seen={},list=[];
  tokens.forEach(function(token){
    var t=txtThreeDingUnmaskToken(token);
    if(t&&!seen[t]){seen[t]=true;list.push(t);}
  });
  list.sort(function(a,b){return a<b?-1:a>b?1:0;});
  // limit 只允许用于预览；正式输出必须传 null/undefined
  if(limit!=null)list=list.slice(0,Math.max(0,Number(limit||0)));
  if(!list.length)return "";
  return list.map(function(code){return txtThreeDingMaskToken(code,combo,"x");}).join(",")+"各";
}

function txtThreeDingSortTokens(tokens){
  var seen={},list=[];
  (Array.isArray(tokens)?tokens:[]).forEach(function(token){
    var t=txtThreeDingUnmaskToken(token);
    if(t&&!seen[t]){seen[t]=true;list.push(t);}
  });
  // 三位定长数字串按字典序=数值升序，避免 Number() 丢前导零造成观感跳码
  list.sort(function(a,b){return a<b?-1:a>b?1:0;});
  return list;
}

/** 校验：无重复、严格升序、组数吻合；任何截断/跳码一律失败 */
function txtThreeDingAssertFullSorted(list,expectedCount,label){
  list=Array.isArray(list)?list:[];
  var tag=label||"三定结果";
  if(expectedCount!=null&&list.length!==Number(expectedCount)){
    return {ok:false,message:tag+"组数不符：期望 "+expectedCount+"，实际 "+list.length+"（疑似截断）。"};
  }
  var seen={};
  for(var i=0;i<list.length;i++){
    var code=String(list[i]||"");
    if(!/^\d{3}$/.test(code)){
      return {ok:false,message:tag+"含非法码："+code};
    }
    if(seen[code]){
      return {ok:false,message:tag+"含重复码："+code+"（禁止跳码/截断伪装）。"};
    }
    seen[code]=true;
    if(i>0&&!(list[i-1]<code)){
      return {ok:false,message:tag+"未严格升序：…"+list[i-1]+","+code+"…（禁止跳码乱序）。"};
    }
  }
  return {ok:true};
}

/** 最多 800 组分两次复制：先 300，再剩余；每段独立「逗号+各」 */
function txtThreeDingCopyParts(tokens,firstSize,combo){
  var list=txtThreeDingSortTokens(tokens);
  var n=Math.max(1,Number(firstSize||300)||300);
  var part1=list.slice(0,n);
  var part2=list.slice(n);
  return {
    tokens:list,
    part1Tokens:part1,
    part2Tokens:part2,
    part1Text:txtFormatThreeDingGeList(part1,null,combo),
    part2Text:txtFormatThreeDingGeList(part2,null,combo),
    part1Count:part1.length,
    part2Count:part2.length,
    total:list.length
  };
}

function txtThreeDingCodeSum(code){
  var total=0;
  String(code||"").split("").forEach(function(digit){total+=Number(digit||0);});
  return total;
}

function txtThreeDingCodeSpan(code){
  var nums=String(code||"").split("").map(Number).filter(function(n){return isFinite(n);});
  if(nums.length!==3)return null;
  return Math.max.apply(null,nums)-Math.min.apply(null,nums);
}

function txtThreeDingPassesRule(code3,rule,combo){
  code3=String(code3||"");
  if(!/^\d{3}$/.test(code3)||!rule)return false;
  combo=combo||txtThreeDingComboByName("千百十");
  for(var i=0;i<3;i++){
    var key=THREE_DING_POS_KEYS[combo.positions[i]];
    if(!txtShotContains(rule[key],code3.charAt(i)))return false;
  }
  var a=code3.charAt(0),b=code3.charAt(1),c=code3.charAt(2);
  var hewei=txtShotHewei(a,b,c);
  // 文中出现的三位合尾均作用于当前三定三位（默认全码则恒过）
  if(!txtShotContains(rule.qianBaiShiHewei,hewei))return false;
  if(!txtShotContains(rule.baiShiGeHewei,hewei))return false;
  if(!txtShotContains(rule.qianBaiGeHewei,hewei))return false;
  if(!txtShotContains(rule.wanBaiShiHewei||"0123456789".split(""),hewei))return false;
  // 二数合按三定三位两两合尾
  if(!txtShotAnyTwoHeweiHit(code3+"0",rule.twoHeweiHit,[0,1,2]))return false;
  var shapes=rule.shapes||[];
  for(var s=0;s<shapes.length;s++){
    var shape=String(shapes[s]||"");
    if(shape.length===3){
      if(txtShotCodeMatchesShape(code3+"0",shape))return false;
    }else if(shape.length===4){
      if(txtShotCodeMatchesShape(code3+"0",shape.slice(0,3)))return false;
    }
  }
  var pairs=rule.pairs||[];
  for(var p=0;p<pairs.length;p++){
    if(txtShotCodeContainsPair(code3,pairs[p]))return false;
  }
  var hitPairs=rule.hitPairs||[];
  for(var h=0;h<hitPairs.length;h++){
    if(!txtShotCodeContainsPair(code3,hitPairs[h]))return false;
  }
  var sumAvoid=rule.sumAvoid||[];
  for(var sv=0;sv<sumAvoid.length;sv++){
    if(txtThreeDingCodeSum(code3)===Number(sumAvoid[sv]))return false;
  }
  var sumRangeAvoid=rule.sumRangeAvoid||[];
  for(var sr=0;sr<sumRangeAvoid.length;sr++){
    var range=txtRangeObject(sumRangeAvoid[sr]);
    var sum=txtThreeDingCodeSum(code3);
    if(range&&sum>=range.start&&sum<=range.end)return false;
  }
  var spanAvoid=rule.spanAvoid||[];
  for(var sp=0;sp<spanAvoid.length;sp++){
    if(String(txtThreeDingCodeSpan(code3))===String(spanAvoid[sp]))return false;
  }
  return true;
}

function txtThreeDingFilterCandidates(rule,combo){
  var out=[];
  for(var i=0;i<1000;i++){
    var code=("000"+i).slice(-3);
    if(txtThreeDingPassesRule(code,rule,combo))out.push(code);
  }
  return out;
}

function txtRandomIntInclusive(min,max){
  min=Number(min);max=Number(max);
  if(!isFinite(min)||!isFinite(max)||max<min)return min;
  return min+Math.floor(Math.random()*(max-min+1));
}

function txtThreeDingHistoryCode(row,combo){
  combo=combo||txtThreeDingComboByName("千百十");
  var nums=Array.isArray(row&&row.numbers)?row.numbers:[];
  if(nums.length<3)return "";
  var needGe=combo.positions.indexOf(3)>=0;
  if(needGe&&nums.length<4)return "";
  if(combo.positions.some(function(pos){return pos<0||pos>3;})){
    return "";
  }
  var code=combo.positions.map(function(pos){
    return String(nums[pos]==null?"":nums[pos]).replace(/\D/g,"").charAt(0)||"";
  }).join("");
  return /^\d{3}$/.test(code)?code:"";
}
function txtValidateThreeDingHistory(history){
  var rows=Array.isArray(history)?history:[];
  var periods=[];
  for(var i=0;i<rows.length;i++){
    var row=rows[i]||{};
    var period=String(row.period||row.issue||row.uid||"");
    if(!/^\d+$/.test(period))return "RDS 历史第"+(i+1)+"条期号无效，已停止回删。";
    if(!Array.isArray(row.numbers)||row.numbers.length<4||row.numbers.slice(0,4).some(function(value){return !/^\d$/.test(String(value));})){
      return "RDS 历史第"+(i+1)+"条号码字段无效，已停止回删。";
    }
    periods.push(period);
  }
  if(Object.keys(periods.reduce(function(set,period){set[period]=true;return set;},{})).length!==periods.length){
    return "RDS 历史存在重复期号，已停止回删。";
  }
  for(var j=1;j<periods.length;j++)if(Number(periods[j-1])<=Number(periods[j]))return "RDS 历史不是严格最新到最旧排序，已停止回删。";
  return "";
}

function txtThreeDingResultMeta(combo,rule,extra){
  extra=extra&&typeof extra==="object"?extra:{};
  var out={
    ok:true,
    width:3,
    comboName:combo.name,
    blankIndex:combo.blankIndex,
    positions:combo.positions.slice(),
    placeholder:"x"
  };
  Object.keys(extra).forEach(function(key){out[key]=extra[key];});
  return out;
}

/**
 * 将「各」封尾的多段三定攻击语拆开。多段输入必须逐段通过三定识别，
 * 任一段无效都明确报错，禁止静默丢段后继续生成不完整组合。
 */
function txtThreeDingIndependentSegments(raw){
  var source=String(raw==null?"":raw);
  var rawParts=source.split(/各+/).map(function(part){return String(part||"").trim();}).filter(Boolean);
  var parts=[];
  rawParts.forEach(function(part){
    if(parts.length){
      var hasPositionBlock=/(?:千|百|十|个)\d/.test(part);
      // 「各除...」「各含...」是上一段的追加尾条件，不是第二个三定组合。
      // 只有合并后仍能通过完整三定解析，才允许自动并回；其他无效内容继续报错。
      if(!hasPositionBlock&&txtNormalizeThreeDingAttackText(parts[parts.length-1]+part).ok){
        parts[parts.length-1]+=part;
        return;
      }
    }
    parts.push(part);
  });
  if(parts.length<2)return {ok:true,segments:[parts[0]||source]};
  var invalid=[];
  parts.forEach(function(part,index){
    var prepared=txtNormalizeThreeDingAttackText(part);
    if(!prepared||!prepared.ok){
      invalid.push("第"+(index+1)+"段"+(prepared&&prepared.error?("（"+prepared.error+"）"):""));
    }
  });
  if(invalid.length){
    return {
      ok:false,
      message:"多段三定存在无效内容："+invalid.join("、")+"。已停止转换，不会忽略该段；请修正后重试。"
    };
  }
  return {ok:true,segments:parts};
}

/**
 * 三定单组合：完整 1000 组用 RDS 真实旧码回删 200、剩 800；过滤后不超过 800 原样保留。
 * 支持千百十 / 百十个 / 千十个(万十个) / 千百个；空位输出 x。
 * history: [{period, numbers:[q,b,s,g]}, ...] 最新在前
 */
function convertThreeDingBackDeleteSingle(raw,history,options){
  options=options&&typeof options==="object"?options:{};
  var day=options.day||txtTodayKey();
  var text=String(raw==null?"":raw);
  if(options.panelThreeDuishuPair)text=txtApplyPanelThreeDuishuPair(text,options.panelThreeDuishuPair);
  else text=txtCanonicalizeAttackHeweiAndDuishu(text);
  if(!text.trim())return {ok:false,message:"请粘贴三定攻击语后再转换。"};
  var prepared=txtNormalizeThreeDingAttackText(text);
  if(prepared&&prepared.error)return {ok:false,message:prepared.error};
  if(!prepared||!prepared.ok)return {ok:false,message:"未识别为三定攻击语。支持千百十 / 百十个 / 千十个(万十个) / 千百个；空位为 x。"};
  var combo=prepared.combo;
  var rule=txtShotRuleFromText(prepared.normalized,{twoHeweiScopeIndexes:[0,1,2]});
  if(!rule)return {ok:false,message:"未识别为三定攻击语。示例：百134…+123…个123…三定千百十合…除对数06"};
  if(rule.error)return {ok:false,message:rule.error};
  var filtered=txtThreeDingFilterCandidates(rule,combo);
  var afterFilter=filtered.length;
  if(!afterFilter)return {ok:false,message:"排除后无剩余号码，请检查攻击语。"};

  // 旧版 options.target/remainMin/remainMax 不再影响结果，防止入口覆盖统一目标。
  var target=Math.min(afterFilter,THREE_DING_REMAIN_TARGET);

  var set={};
  filtered.forEach(function(code){set[code]=true;});
  var remaining=afterFilter;
  var deletedUnique=0;
  var scanned=0;
  var seenOld={};
  // 对齐面板：从最新往更早扫，重复旧码跳过并继续补扫，直到唯一删满目标或历史用尽（不限死只扫200行）
  var rows=txtThreeDingEnsureNewestFirst(history);
  var historyError=txtValidateThreeDingHistory(rows.slice(0,Math.min(rows.length,THREE_DING_HISTORY_SCAN_LIMIT)));
  if(historyError)return {ok:false,message:historyError};
  if(remaining>target&&rows.length<THREE_DING_HISTORY_MIN){
    return {ok:false,message:"真实旧号不足：RDS 历史不足 "+THREE_DING_HISTORY_MIN+" 期，无法可靠完成真实旧码回删；当前仅 "+rows.length+" 期，已停止，不截断凑数。"};
  }
  if(remaining>target&&(!rows.length)){
    return {ok:false,message:"无 RDS 历史，无法真实回删。禁止截断凑数。"};
  }

  for(var i=0;i<rows.length&&remaining>target;i++){
    scanned+=1;
    var oldCode=txtThreeDingHistoryCode(rows[i],combo);
    if(!oldCode||seenOld[oldCode])continue;
    seenOld[oldCode]=true;
    if(!set[oldCode])continue;
    delete set[oldCode];
    remaining-=1;
    deletedUnique+=1;
  }

  if(remaining!==target){
    return {
      ok:false,
      message:"真实旧号不足：排除后 "+afterFilter+" 组，目标剩 "+target+" 组，已删唯一旧码 "+deletedUnique+" 个（扫 "+scanned+" 期/池"+rows.length+"），仍剩 "+remaining+" 组；不做截断、不伪造删号、不跳码凑数。请加大历史窗口或减少排除。"
    };
  }
  if(afterFilter>target&&deletedUnique!==(afterFilter-target)){
    return {ok:false,message:"回删未真正完成：应删 "+(afterFilter-target)+"，实删 "+deletedUnique+"；拒绝截断假结果。"};
  }

  var left=txtThreeDingSortTokens(Object.keys(set));
  var assertLeft=txtThreeDingAssertFullSorted(left,target,"回删结果");
  if(!assertLeft.ok)return {ok:false,message:assertLeft.message};
  if(left.length!==target||deletedUnique!==(afterFilter-left.length)){
    return {ok:false,message:"回删计数不一致：已删 "+deletedUnique+"，排除后 "+afterFilter+"，剩余 "+left.length+"，目标 "+target+"（拒绝截断结果）。"};
  }
  if(afterFilter===THREE_DING_FULL_GROUP_SIZE&&(
    deletedUnique!==THREE_DING_DELETE_TARGET||left.length!==THREE_DING_REMAIN_TARGET
  )){
    return {
      ok:false,
      message:combo.name+"三定完整组断言失败：必须真实回删 "+THREE_DING_DELETE_TARGET+
        " 组、现剩 "+THREE_DING_REMAIN_TARGET+" 组。"
    };
  }
  var outText=txtFormatThreeDingGeList(left,null,combo);
  var copyParts=txtThreeDingCopyParts(left,300,combo);
  // 正式 text / attackText 必须全量；分段复制是额外字段，不是截断后的主结果
  return txtThreeDingResultMeta(combo,rule,{
    count:left.length,
    tokens:left,
    text:outText,
    preview:txtFormatThreeDingGeList(left,120,combo),
    attackText:outText,
    copyParts:copyParts,
    fileName:txtFileNameByCount(3,day,left.length),
    source:"three-ding-backdelete-fixed-800",
    label:combo.name+"三定转换（回删200剩800）",
    afterFilter:afterFilter,
    targetRemain:target,
    deletedUnique:deletedUnique,
    scanned:scanned,
    realBackDelete:deletedUnique>0,
    truncated:false,
    note:combo.name+"三定排除后 "+afterFilter+" 组 → "+(afterFilter>THREE_DING_REMAIN_TARGET?("真实旧码回删至 "+THREE_DING_REMAIN_TARGET):"不超过800原样保留")+"，已删唯一旧码 "+deletedUnique+"，扫 "+scanned+" 期（池"+rows.length+"，重复往更早补），现剩 "+left.length+" 组（完整1000组固定回删200剩800；不补码、无截断无跳码）；空位 x；复制请用「复制全部」或下载TXT；"+txtShotRuleSummary(rule)
  });
}

function txtThreeDingMultiBackDeleteResult(groups,day){
  var list=(Array.isArray(groups)?groups:[]).filter(function(group){return group&&group.ok;});
  var total=list.reduce(function(sum,group){return sum+Number(group.count||0);},0);
  var afterFilter=list.reduce(function(sum,group){return sum+Number(group.afterFilter||0);},0);
  var deletedUnique=list.reduce(function(sum,group){return sum+Number(group.deletedUnique||0);},0);
  var scanned=list.reduce(function(sum,group){return sum+Number(group.scanned||0);},0);
  return {
    ok:true,
    width:3,
    multiCombo:true,
    groups:list,
    count:total,
    fileName:txtFileNameByCount(3,day,total),
    source:"three-ding-backdelete-multi-combo",
    label:"三定多位置组合转换（独立真实回删）",
    afterFilter:afterFilter,
    deletedUnique:deletedUnique,
    scanned:scanned,
    truncated:false,
    note:"已按位置组合独立过滤、独立使用对应三位 RDS 旧码回删；各组合不会交叉叠加删除条件。请选择下方组合后分别复制或下载。"
  };
}

/**
 * 多组合入口：官方攻击语以「各」封尾时，每个有效位置组合必须独立过滤、独立回删。
 * 单组合仍走原返回格式，保持既有页面和复制逻辑兼容。
 */
function convertThreeDingBackDelete(raw,history,options){
  options=options&&typeof options==="object"?options:{};
  var split=txtThreeDingIndependentSegments(raw);
  if(!split||!split.ok)return {ok:false,message:String(split&&split.message||"多段三定拆分失败。")};
  var segments=split.segments;
  if(segments.length<=1)return convertThreeDingBackDeleteSingle(raw,history,options);
  var groups=[];
  for(var i=0;i<segments.length;i++){
    var result=convertThreeDingBackDeleteSingle(segments[i],history,options);
    if(!result||!result.ok){
      var prepared=txtNormalizeThreeDingAttackText(segments[i]);
      var name=prepared&&prepared.combo&&prepared.combo.name?prepared.combo.name:("第"+(i+1)+"段");
      return {ok:false,message:name+"三定独立回删失败："+String(result&&result.message||"未知错误")};
    }
    groups.push(result);
  }
  return txtThreeDingMultiBackDeleteResult(groups,options.day||txtTodayKey());
}

/**
 * 本机回删口径（恢复旧手感）：
 * 1) 本机先按攻击语生成/排除
 * 2) 只删「今日」开奖命中（opentime 日期或期号前8位 YYYYMMDD）
 * 3) 498 是底线：不少于 498；今日命中删完就停，不硬磨到 498
 * 例：今日开过 485x，剩余有 485 → 删 485；若再删会低于 498 则不再删
 */
var THREE_DING_HIT_DELETE_FLOOR=498;

function txtThreeDingEnsureNewestFirst(rows){
  var list=Array.isArray(rows)?rows.slice():[];
  list.sort(function(a,b){
    return Number(String((b&&(b.period||b.issue||b.uid))||0)) - Number(String((a&&(a.period||a.issue||a.uid))||0));
  });
  return list;
}

/** 历史行归属日：优先 opentime 的 YYYY-MM-DD，否则期号前 8 位 */
function txtThreeDingRowDayKey(row){
  if(!row||typeof row!=="object")return "";
  var ot=String(row.opentime||row.openTime||row.open_time||"");
  var m=ot.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(m)return m[1]+m[2]+m[3];
  var p=String(row.period||row.issue||row.uid||"");
  if(/^\d{8}/.test(p))return p.slice(0,8);
  return "";
}

/** 今日日键：options.day → 最新一期归属日 → 本机日历日 */
function txtThreeDingResolveTodayKey(rows,options){
  options=options&&typeof options==="object"?options:{};
  var forced=String(options.day||options.todayKey||"").replace(/\D/g,"");
  if(/^\d{8}$/.test(forced))return forced;
  var list=Array.isArray(rows)?rows:[];
  for(var i=0;i<list.length;i++){
    var k=txtThreeDingRowDayKey(list[i]);
    if(/^\d{8}$/.test(k))return k;
  }
  return txtTodayKey();
}

function txtThreeDingFilterTodayRows(rows,dayKey){
  var key=String(dayKey||"");
  var list=Array.isArray(rows)?rows:[];
  if(!/^\d{8}$/.test(key))return [];
  return list.filter(function(row){return txtThreeDingRowDayKey(row)===key;});
}

function convertThreeDingHistoryHitDeleteSingle(raw,history,options){
  options=options&&typeof options==="object"?options:{};
  var floor=Math.max(0,Number(options.remainFloor!=null?options.remainFloor:THREE_DING_HIT_DELETE_FLOOR)||THREE_DING_HIT_DELETE_FLOOR);
  var text=String(raw==null?"":raw);
  if(options.panelThreeDuishuPair)text=txtApplyPanelThreeDuishuPair(text,options.panelThreeDuishuPair);
  else text=txtCanonicalizeAttackHeweiAndDuishu(text);
  if(!text.trim())return {ok:false,message:"请粘贴三定攻击语后再转换。"};
  var prepared=txtNormalizeThreeDingAttackText(text);
  if(prepared&&prepared.error)return {ok:false,message:prepared.error};
  if(!prepared||!prepared.ok)return {ok:false,message:"未识别为三定攻击语。支持千百十 / 百十个 / 千十个(万十个) / 千百个；空位为 x。"};
  var combo=prepared.combo;
  var rule=txtShotRuleFromText(prepared.normalized,{twoHeweiScopeIndexes:[0,1,2]});
  if(!rule)return {ok:false,message:"未识别为三定攻击语。"};
  if(rule.error)return {ok:false,message:rule.error};
  var filtered=txtThreeDingFilterCandidates(rule,combo);
  var afterFilter=filtered.length;
  if(!afterFilter)return {ok:false,message:"排除后无剩余号码，请检查攻击语。"};

  var set={};
  filtered.forEach(function(code){set[code]=true;});
  var remaining=afterFilter;
  var deletedUnique=0;
  var scanned=0;
  var hitSamples=[];
  var stoppedAtFloor=false;
  var seenOld={};
  // 仍拉近 200 期做数据校验；真正删除只扫「今日」期
  var pool=txtThreeDingEnsureNewestFirst(history).slice(0,THREE_DING_HISTORY_SCAN_LIMIT);
  var historyError=txtValidateThreeDingHistory(pool);
  if(historyError)return {ok:false,message:historyError};
  if(!pool.length)return {ok:false,message:"无历史，无法回删。"};
  if(pool.length<THREE_DING_HISTORY_MIN){
    return {ok:false,message:"RDS 历史不足 "+THREE_DING_HISTORY_MIN+" 期（当前 "+pool.length+"），无法回删。"};
  }
  var day=txtThreeDingResolveTodayKey(pool,options);
  var rows=txtThreeDingFilterTodayRows(pool,day);
  if(!rows.length){
    return {ok:false,message:"今日（"+day+"）无开奖记录可回删；请确认历史已刷新到当日。"};
  }

  for(var i=0;i<rows.length;i++){
    scanned+=1;
    var oldCode=txtThreeDingHistoryCode(rows[i],combo);
    if(!oldCode)continue;
    if(seenOld[oldCode])continue;
    seenOld[oldCode]=true;
    if(!set[oldCode])continue;
    // 不少于 floor：再删会低于底线则停，不硬磨到 floor
    if(remaining-1<floor){
      stoppedAtFloor=true;
      break;
    }
    delete set[oldCode];
    remaining-=1;
    deletedUnique+=1;
    if(hitSamples.length<20){
      hitSamples.push(String(rows[i].period||"")+"→"+oldCode);
    }
  }

  var left=txtThreeDingSortTokens(Object.keys(set));
  var outText=txtFormatThreeDingGeList(left,null,combo);
  var copyParts=txtThreeDingCopyParts(left,300,combo);
  var frontPeriod=rows[0]&&rows[0].period?String(rows[0].period):"";
  var backPeriod=rows[rows.length-1]&&rows[rows.length-1].period?String(rows[rows.length-1].period):"";
  return txtThreeDingResultMeta(combo,rule,{
    count:left.length,
    tokens:left,
    codes:left,
    text:outText,
    preview:txtFormatThreeDingGeList(left,120,combo),
    attackText:outText,
    copyParts:copyParts,
    fileName:txtFileNameByCount(3,day,left.length),
    source:"three-ding-today-hit-delete-floor",
    label:combo.name+"三定转换（今日命中删 · 不少于"+floor+"）",
    afterFilter:afterFilter,
    remainFloor:floor,
    todayKey:day,
    todayPeriodCount:rows.length,
    deletedUnique:deletedUnique,
    scanned:scanned,
    scanFromPeriod:frontPeriod,
    scanToPeriod:backPeriod,
    hitSamples:hitSamples,
    stoppedAtFloor:stoppedAtFloor,
    realBackDelete:deletedUnique>0,
    truncated:false,
    note:combo.name+"本机排除后 "+afterFilter+" 组 → 今日"+day+"共"+rows.length+"期（"+frontPeriod+"→"+backPeriod+"）命中删 "+deletedUnique+" 组，现剩 "+left.length+" 组"+(stoppedAtFloor?("（触底线"+floor+"未再删）"):"（今日扫完，不少于"+floor+"）")+"；只删今日开过且仍在剩余里的码；"+txtShotRuleSummary(rule)
  });
}

function convertThreeDingHistoryHitDelete(raw,history,options){
  options=options&&typeof options==="object"?options:{};
  var split=txtThreeDingIndependentSegments(raw);
  if(!split||!split.ok)return {ok:false,message:String(split&&split.message||"多段三定拆分失败。")};
  var segments=split.segments;
  if(segments.length<=1)return convertThreeDingHistoryHitDeleteSingle(raw,history,options);
  var groups=[];
  for(var i=0;i<segments.length;i++){
    var result=convertThreeDingHistoryHitDeleteSingle(segments[i],history,options);
    if(!result||!result.ok){
      var prepared=txtNormalizeThreeDingAttackText(segments[i]);
      var name=prepared&&prepared.combo&&prepared.combo.name?prepared.combo.name:("第"+(i+1)+"段");
      return {ok:false,message:name+"三定今日命中回删失败："+String(result&&result.message||"未知错误")};
    }
    groups.push(result);
  }
  var multi=txtThreeDingMultiBackDeleteResult(groups,options.day||txtTodayKey());
  multi.source="three-ding-today-hit-delete-multi";
  multi.label="三定多位置组合转换（今日命中删 · 不少于498）";
  multi.note="本机先生成；各组合独立只删今日命中，不少于498。";
  multi.remainFloor=THREE_DING_HIT_DELETE_FLOOR;
  return multi;
}

/** 本机「先生成」：只按攻击语排除，不扫历史、不回删 */
function convertThreeDingGenerateOnlySingle(raw,options){
  options=options&&typeof options==="object"?options:{};
  var day=options.day||txtTodayKey();
  var text=String(raw==null?"":raw);
  if(options.panelThreeDuishuPair)text=txtApplyPanelThreeDuishuPair(text,options.panelThreeDuishuPair);
  else text=txtCanonicalizeAttackHeweiAndDuishu(text);
  if(!text.trim())return {ok:false,message:"请粘贴三定攻击语后再转换。"};
  var prepared=txtNormalizeThreeDingAttackText(text);
  if(prepared&&prepared.error)return {ok:false,message:prepared.error};
  if(!prepared||!prepared.ok)return {ok:false,message:"未识别为三定攻击语。支持千百十 / 百十个 / 千十个(万十个) / 千百个；空位为 x。"};
  var combo=prepared.combo;
  var rule=txtShotRuleFromText(prepared.normalized,{twoHeweiScopeIndexes:[0,1,2]});
  if(!rule)return {ok:false,message:"未识别为三定攻击语。"};
  if(rule.error)return {ok:false,message:rule.error};
  var filtered=txtThreeDingFilterCandidates(rule,combo);
  var afterFilter=filtered.length;
  if(!afterFilter)return {ok:false,message:"排除后无剩余号码，请检查攻击语。"};
  var left=txtThreeDingSortTokens(filtered);
  var outText=txtFormatThreeDingGeList(left,null,combo);
  var copyParts=txtThreeDingCopyParts(left,300,combo);
  return txtThreeDingResultMeta(combo,rule,{
    count:left.length,
    tokens:left,
    codes:left,
    text:outText,
    preview:txtFormatThreeDingGeList(left,120,combo),
    attackText:outText,
    copyParts:copyParts,
    fileName:txtFileNameByCount(3,day,left.length),
    source:"three-ding-generate-only",
    label:combo.name+"三定生成（尚未回删）",
    afterFilter:afterFilter,
    deletedUnique:0,
    scanned:0,
    remainFloor:THREE_DING_HIT_DELETE_FLOOR,
    realBackDelete:false,
    truncated:false,
    note:combo.name+"本机排除后 "+afterFilter+" 组（尚未回删；回删将只删今日命中、不少于"+THREE_DING_HIT_DELETE_FLOOR+"）；"+txtShotRuleSummary(rule)
  });
}

function convertThreeDingGenerateOnly(raw,options){
  options=options&&typeof options==="object"?options:{};
  var split=txtThreeDingIndependentSegments(raw);
  if(!split||!split.ok)return {ok:false,message:String(split&&split.message||"多段三定拆分失败。")};
  var segments=split.segments;
  if(segments.length<=1)return convertThreeDingGenerateOnlySingle(raw,options);
  var groups=[];
  for(var i=0;i<segments.length;i++){
    var result=convertThreeDingGenerateOnlySingle(segments[i],options);
    if(!result||!result.ok){
      var prepared=txtNormalizeThreeDingAttackText(segments[i]);
      var name=prepared&&prepared.combo&&prepared.combo.name?prepared.combo.name:("第"+(i+1)+"段");
      return {ok:false,message:name+"三定生成失败："+String(result&&result.message||"未知错误")};
    }
    groups.push(result);
  }
  var multi=txtThreeDingMultiBackDeleteResult(groups,options.day||txtTodayKey());
  multi.source="three-ding-generate-only-multi";
  multi.label="三定多位置组合生成（尚未回删）";
  multi.note="本机已生成候选；请再点回删（今日命中删 · 不少于498）。";
  multi.remainFloor=THREE_DING_HIT_DELETE_FLOOR;
  return multi;
}

function downloadTxt(fileName,text){
  var name=String(fileName||"attack.txt").replace(/[\\/:*?"<>|\u0000-\u001f]+/g,"_").trim()||"attack.txt";
  if(!/\.txt$/i.test(name))name+=".txt";
  var blob=new Blob([String(text||"")],{type:"text/plain;charset=utf-8"});
  if(typeof navigator!=="undefined"&&typeof navigator.msSaveOrOpenBlob==="function"){
    navigator.msSaveOrOpenBlob(blob,name);return name;
  }
  var url=URL.createObjectURL(blob);
  var a=document.createElement("a");
  a.href=url;a.download=name;a.rel="noopener";a.style.display="none";
  document.body.appendChild(a);a.click();
  setTimeout(function(){try{URL.revokeObjectURL(url);}catch(e){} try{a.remove();}catch(e2){}},800);
  return name;
}
global.Penta5AttackTxtConvert={
  THREE_DING_BACKDELETE_VERSION:"20260725",
  convert:convertAttackText,
  convertThreeDingBackDelete:convertThreeDingBackDelete,
  convertThreeDingHistoryHitDelete:convertThreeDingHistoryHitDelete,
  formatThreeDingGeList:txtFormatThreeDingGeList,
  threeDingCopyParts:txtThreeDingCopyParts,
  threeDingSortTokens:txtThreeDingSortTokens,
  threeDingAssertFullSorted:txtThreeDingAssertFullSorted,
  threeDingMaskToken:txtThreeDingMaskToken,
  threeDingUnmaskToken:txtThreeDingUnmaskToken,
  looksLikeThreeDing:txtLooksLikeThreeDingAttack,
  download:downloadTxt,
  looksLikeFull:txtShotLooksLikeFullModel,
  looksLikeShort:txtShotLooksLikeShortExclusionCommand,
  canonicalizeAttackText:txtCanonicalizeAttackHeweiAndDuishu,
  applyPanelThreeDuishuPair:txtApplyPanelThreeDuishuPair,
  THREE_DING_FULL_GROUP_SIZE:THREE_DING_FULL_GROUP_SIZE,
  THREE_DING_DELETE_TARGET:THREE_DING_DELETE_TARGET,
  THREE_DING_REMAIN_TARGET:THREE_DING_REMAIN_TARGET,
  THREE_DING_HISTORY_MIN:THREE_DING_HISTORY_MIN,
  THREE_DING_HISTORY_SCAN_LIMIT:THREE_DING_HISTORY_SCAN_LIMIT,
  THREE_DING_REMAIN_MIN:THREE_DING_REMAIN_MIN,
  THREE_DING_REMAIN_MAX:THREE_DING_REMAIN_MAX,
  THREE_DING_HIT_DELETE_FLOOR:THREE_DING_HIT_DELETE_FLOOR,
  THREE_DING_COMBOS:THREE_DING_COMBOS
};
})(typeof window!=="undefined"?window:this);
