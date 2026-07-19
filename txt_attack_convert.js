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
function txtRecentExactCodeList(limit){
  if(typeof global.__penta5AttackTxtRecentExactCodeList==="function"){
    try{return global.__penta5AttackTxtRecentExactCodeList(limit)||[];}catch(e){return [];}
  }
  return [];
}
function recentExactCodeWindowIsMax(value){
  return String(value||"").toLowerCase()==="max"||/最大|全部|MAX/i.test(String(value||""));
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

function txtShotExcludeRecentExactWindows(text){
  var out=[],match,regex=/除(?:旧码|重复码|历史码)(300|500|800|1000|1300|最大|全部|MAX)/gi;
  while((match=regex.exec(String(text||"")))){
    var value=recentExactCodeWindowIsMax(match[1])?"最大":match[1];
    if(out.indexOf(value)<0)out.push(value);
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
  if(/(?:二数合|三数合|和值|跨度|旧码|重复码|历史码|对数)/.test(raw))return true;
  return false;
}

function txtShotRuleFromText(text,options){
  var raw=txtShotNormalize(text);
  if(!txtShotLooksLikeFullModel(raw))return null;
  var opts=options&&typeof options==="object"?options:{};
  var tailEnds=["千百十二数合","二数合","三数合","取二兄弟","兄弟号","除","含","中","各"];
  var heweiEnds=["千百十合","千百合","千百个合","百十个合","千百十个合"].concat(tailEnds);
  var rule={
    raw:raw,
    qian:txtShotRequiredDigits(raw,"千",["百"]),
    bai:txtShotRequiredDigits(raw,"百",["十"]),
    shi:txtShotRequiredDigits(raw,"十",["个"]),
    ge:txtShotRequiredDigits(raw,"个",["四定"].concat(heweiEnds)),
    // 千百十合；兼容别名「千百合」
    qianBaiShiHewei:txtShotOptionalDigitsFirst(raw,["千百十合","千百合"],["千百个合","百十个合","千百十个合"].concat(tailEnds)),
    // 千百个合：千+百+个（跳过十位）
    qianBaiGeHewei:txtShotOptionalDigits(raw,"千百个合",["百十个合","千百十个合"].concat(tailEnds)),
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
    recentExactCodeAvoid:txtShotExcludeRecentExactWindows(raw),
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

function txtShotPrepareRecentExactLookups(rule){
  var lookups=Object.create(null);
  (rule&&Array.isArray(rule.recentExactCodeAvoid)?rule.recentExactCodeAvoid:[]).forEach(function(limit){
    var key=String(limit);
    var lookup=Object.create(null);
    txtRecentExactCodeList(limit).forEach(function(code){lookup[String(code)]=true;});
    lookups[key]=lookup;
  });
  return lookups;
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
  for(var old=0;old<rule.recentExactCodeAvoid.length;old++){
    var oldLimit=rule.recentExactCodeAvoid[old];
    var oldLookup=rule._recentExactCodeLookups&&rule._recentExactCodeLookups[String(oldLimit)];
    if(oldLookup?oldLookup[String(code)]===true:txtRecentExactCodeList(oldLimit).indexOf(String(code))>=0)return false;
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
  var previousLookups=rule._recentExactCodeLookups;
  rule._recentExactCodeLookups=txtShotPrepareRecentExactLookups(rule);
  try{
    for(var i=0;i<10000;i++){
      var code=("0000"+i).slice(-4);
      if(txtShotPassesRule(code,rule))out.push(code);
    }
  }finally{
    if(previousLookups===undefined)delete rule._recentExactCodeLookups;
    else rule._recentExactCodeLookups=previousLookups;
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
    +(rule.recentExactCodeAvoid.length?" 除旧码"+rule.recentExactCodeAvoid.join("、除旧码"):"")
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
  var text=String(raw==null?"":raw);
  if(!text.trim())return {ok:false,message:"请粘贴攻击语后再转换。"};
  var shot=txtShotResultFromInput(text,day,options);
  if(shot)return shot;
  var shortShot=txtShortShotResultFromInput(text,day);
  if(shortShot)return shortShot;
  return {ok:false,message:"未识别为完整攻击语。请粘贴含「千…百…十…个…四定」的文本，或短指令如「除千1除对数79」。"};
}

var THREE_DING_REMAIN_MIN=490;
var THREE_DING_REMAIN_MAX=499;
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
  if(present.length===4)return txtThreeDingComboByName("千百十");
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
  var heweiMarks=["千百十合","千百合","千百个合","百十个合","千百十个合","千百十二数合","二数合","三数合","除","含对数","中对数","对数"];
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
  var prep=txtThreeDingPreprocess(raw);
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
  list.sort(function(a,b){return Number(a)-Number(b);});
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
  list.sort(function(a,b){return Number(a)-Number(b);});
  return list;
}

/** 499 组分两次复制：先 300，再剩余；每段独立「逗号+各」 */
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
  var code=combo.positions.map(function(pos){
    return String(nums[pos]==null?"":nums[pos]).replace(/\D/g,"").charAt(0)||"";
  }).join("");
  return /^\d{3}$/.test(code)?code:"";
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
 * 三定：先按攻击语排除，再用 RDS 真实旧码（对应三位）回删到 490–499。
 * 支持千百十 / 百十个 / 千十个(万十个) / 千百个；空位输出 x。
 * history: [{period, numbers:[q,b,s,g]}, ...] 最新在前
 */
function convertThreeDingBackDelete(raw,history,options){
  options=options&&typeof options==="object"?options:{};
  var day=options.day||txtTodayKey();
  var text=String(raw==null?"":raw);
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

  var remainMin=Number(options.remainMin!=null?options.remainMin:THREE_DING_REMAIN_MIN);
  var remainMax=Number(options.remainMax!=null?options.remainMax:THREE_DING_REMAIN_MAX);
  if(!isFinite(remainMin))remainMin=THREE_DING_REMAIN_MIN;
  if(!isFinite(remainMax))remainMax=THREE_DING_REMAIN_MAX;
  if(remainMax<remainMin){var swap=remainMin;remainMin=remainMax;remainMax=swap;}

  var target;
  if(options.target!=null&&isFinite(Number(options.target))){
    target=Math.max(remainMin,Math.min(remainMax,Math.floor(Number(options.target))));
  }else if(afterFilter<remainMin){
    var tokensLow=txtThreeDingSortTokens(filtered);
    var textLow=txtFormatThreeDingGeList(tokensLow,null,combo);
    var partsLow=txtThreeDingCopyParts(tokensLow,300,combo);
    return txtThreeDingResultMeta(combo,rule,{
      count:tokensLow.length,
      tokens:tokensLow,
      text:textLow,
      preview:txtFormatThreeDingGeList(tokensLow,120,combo),
      attackText:textLow,
      copyParts:partsLow,
      fileName:txtFileNameByCount(3,day,tokensLow.length),
      source:"three-ding-backdelete-underflow",
      label:combo.name+"三定TXT（排除后不足490）",
      afterFilter:afterFilter,
      targetRemain:null,
      deletedUnique:0,
      scanned:0,
      note:combo.name+"三定排除后仅剩 "+afterFilter+" 组，低于 "+remainMin+"，未做旧码回删；空位 x；输出数值升序不跳码；"+txtShotRuleSummary(rule)
    });
  }else if(afterFilter<=remainMax){
    target=txtRandomIntInclusive(remainMin,afterFilter);
  }else{
    target=txtRandomIntInclusive(remainMin,remainMax);
  }

  var set={};
  filtered.forEach(function(code){set[code]=true;});
  var remaining=afterFilter;
  var deletedUnique=0;
  var scanned=0;
  var seenOld={};
  var rows=Array.isArray(history)?history:[];

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

  if(remaining>target){
    return {
      ok:false,
      message:"真实旧号不足：排除后 "+afterFilter+" 组，目标剩 "+target+" 组，已删唯一旧码 "+deletedUnique+" 个（扫 "+scanned+" 期），仍剩 "+remaining+" 组；不做截断、不伪造删号。请加大 /converter-history 窗口或减少排除。"
    };
  }

  var left=txtThreeDingSortTokens(Object.keys(set));
  var outText=txtFormatThreeDingGeList(left,null,combo);
  var copyParts=txtThreeDingCopyParts(left,300,combo);
  return txtThreeDingResultMeta(combo,rule,{
    count:left.length,
    tokens:left,
    text:outText,
    preview:txtFormatThreeDingGeList(left,120,combo),
    attackText:outText,
    copyParts:copyParts,
    fileName:txtFileNameByCount(3,day,left.length),
    source:"three-ding-backdelete-490-499",
    label:combo.name+"三定转换（回删"+remainMin+"-"+remainMax+"）",
    afterFilter:afterFilter,
    targetRemain:target,
    deletedUnique:deletedUnique,
    scanned:scanned,
    note:combo.name+"三定排除后 "+afterFilter+" 组 → 旧码回删目标剩 "+target+"，已删唯一旧码 "+deletedUnique+"，扫 "+scanned+" 期，现剩 "+left.length+" 组；空位 x；输出数值升序不跳码；可分两段复制（先300再剩余）；"+txtShotRuleSummary(rule)
  });
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
  convert:convertAttackText,
  convertThreeDingBackDelete:convertThreeDingBackDelete,
  formatThreeDingGeList:txtFormatThreeDingGeList,
  threeDingCopyParts:txtThreeDingCopyParts,
  threeDingMaskToken:txtThreeDingMaskToken,
  threeDingUnmaskToken:txtThreeDingUnmaskToken,
  looksLikeThreeDing:txtLooksLikeThreeDingAttack,
  download:downloadTxt,
  looksLikeFull:txtShotLooksLikeFullModel,
  looksLikeShort:txtShotLooksLikeShortExclusionCommand,
  THREE_DING_REMAIN_MIN:THREE_DING_REMAIN_MIN,
  THREE_DING_REMAIN_MAX:THREE_DING_REMAIN_MAX,
  THREE_DING_COMBOS:THREE_DING_COMBOS
};
})(typeof window!=="undefined"?window:this);
