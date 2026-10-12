/* Author-style comparison, purely lexical and descriptive. Never executes C++.
 * UMD, zero dependencies. All thresholds below are coverage guards, not trained
 * classifiers. Encounter-order identifier normalization retains reuse shape; it is not semantic alpha-renaming or scope-aware.
 */
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.StyleEngine = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';
  const VERSION = '1.0.0';
  const MAX_CHARS = 500000;
  const KEYWORDS = new Set(('alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t char16_t char32_t class compl concept const consteval constexpr constinit const_cast continue co_await co_return co_yield decltype default delete do double dynamic_cast else enum explicit export extern false float for friend goto if inline int long mutable namespace new noexcept not not_eq nullptr operator or or_eq private protected public register reinterpret_cast requires return short signed sizeof static static_assert static_cast struct switch template this thread_local throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while xor xor_eq').split(' '));
  const FEATURES = [
    ['control_tight','控制关键字后不加空格','if/for/while/switch/catch 后紧接 ('],
    ['assignment_tight','赋值等号两侧紧凑','单独 = 两侧都无空白'],
    ['comparison_tight','双字符比较符两侧紧凑','== != <= >= 两侧都无空白；不计模板尖括号'],
    ['comma_tight','逗号后不加空格','非末尾逗号后紧接下一个 token'],
    ['paren_brace_tight','右括号与左花括号紧接',') 后紧接 {'],
    ['else_tight','花括号与 else 紧接','} 后紧接 else'],
    ['postfix_increment','后置自增占比','可判别 ++ 中的后置形式；)++ 不自动判别'],
    ['logical_word','逻辑运算使用单词','and/or 相对于 &&/||'],
    ['tab_indent','缩进以 Tab 开头','有缩进的主体有效代码行中首字符为 Tab'],
    ['for_zero','简单 for 从 0 开始','int/long long/auto/size_t 的规范 0/1 初值循环'],
    ['cin_declaration_same_line','声明后同一行 cin','cin >> 前有同一行的简单类型声明'],
    ['cout_one_space','cout 与 << 间单个空格','cout 后紧接首个 << 的格式']
  ].map(([id,label,description])=>({id,label,description}));
  const mean = a => a.length ? a.reduce((x,y)=>x+y,0)/a.length : null;
  function scan(input) {
    if(typeof input !== 'string') throw new TypeError('code must be a string');
    if(input.length>MAX_CHARS) throw new RangeError('代码超过 500,000 字符上限，请缩小文件。');
    const code=input.replace(/\r\n?/g,'\n'), masked=code.split(''), tokens=[], comments=[], warnings=[];
    let i=0,line=1;
    function hide(a,b) { for(let k=a;k<b;k++) if(masked[k]!=='\n') masked[k]=' '; }
    function advance(b) { for(;i<b;i++) if(code[i]==='\n')line++; }
    function push(end,kind) {tokens.push({text:code.slice(i,end),start:i,end,line,kind});advance(end);}
    while(i<code.length) {
      if(/\s/.test(code[i])) {advance(i+1);continue;}
      if(code.startsWith('//',i)) {
        const start=i; let end=i+2;
        while(end<code.length) {if(code[end]==='\n' && code[end-1]!=='\\')break;end++;}
        comments.push({start,end,line,kind:'line'});hide(start,end);advance(end);continue;
      }
      if(code.startsWith('/*',i)) {
        const start=i,close=code.indexOf('*/',i+2),end=close<0?code.length:close+2;
        if(close<0)warnings.push('存在未闭合块注释，尾部已屏蔽；请先核对输入。');
        comments.push({start,end,line,kind:'block'});hide(start,end);advance(end);continue;
      }
      const raw=/^(?:u8|u|U|L)?R"([^ ()\\\t\r\n]{0,16})\(/.exec(code.slice(i,i+22));
      if(raw) {
        const close=code.indexOf(')'+raw[1]+'"',i+raw[0].length),end=close<0?code.length:close+raw[1].length+2;
        if(close<0)warnings.push('存在未闭合原始字符串，尾部已屏蔽；请先核对输入。');
        hide(i,end);push(end,'literal');continue;
      }
      const ordinary=/^(?:u8|u|U|L)?(["'])/.exec(code.slice(i,i+4));
      if(ordinary) {
        let end=i+ordinary[0].length,closed=false;
        while(end<code.length) { if(code[end]==='\\') {end=Math.min(code.length,end+2);continue;} if(code[end++]===ordinary[1]){closed=true;break;} }
        if(!closed)warnings.push('存在未闭合字符串或字符字面量，尾部已屏蔽。');
        hide(i,end);push(end,'literal');continue;
      }
      const id=/^[A-Za-z_$\u0080-\uffff][\w$\u0080-\uffff]*/.exec(code.slice(i));
      if(id) {push(i+id[0].length,'identifier');continue;}
      const num=/^(?:\d|\.\d)(?:[eEpP][+-]|[\w.'])*/.exec(code.slice(i));
      if(num) {push(i+num[0].length,'number');continue;}
      const op=/^(?:<=>|>>=|<<=|->\*|\.\.\.|::|->|\+\+|--|&&|\|\||==|!=|<=|>=|<<|>>|\+=|-=|\*=|\/=|%=|&=|\|=|\^=|\.\*)/.exec(code.slice(i));
      push(i+(op?op[0].length:1),'operator');
    }
    return {code,masked:masked.join(''),tokens,comments,warnings};
  }
  function regions(s) {
    const ranges=[], lines=s.code.split('\n'), masked=s.masked.split('\n');let offset=0,continuation=false;
    function add(start,end,reason){ranges.push({start,end,reason});}
    for(let n=0;n<lines.length;n++) {
      const text=masked[n], original=lines[n];
      if(continuation || /^\s*#/.test(text)) {add(offset,offset+original.length,'预处理指令（含续行）');continuation=/\\\s*$/.test(original);}
      else if(/^\s*using\s+namespace\s+std\s*;\s*$/.test(text) || /^\s*(?:using\s+\w+\s*=\s*(?:(?:unsigned|signed|long|short)\s+)*(?:int|long|char|double|float|bool)|typedef\s+(?:(?:unsigned|signed|long|short)\s+)*(?:int|long|char|double|float|bool)\s+\w+)\s*;\s*$/.test(text)) add(offset,offset+original.length,'标准命名空间 / 类型别名');
      offset+=original.length+1;
    }
    // Only exact, recognisable I/O boilerplate statements, never entire lines.
    const io=/\b(?:ios(?:_base)?\s*::\s*sync_with_stdio\s*\(\s*(?:false|0)\s*\)|(?:std\s*::\s*)?(?:cin|cout)\s*\.\s*tie\s*\(\s*(?:0|nullptr|NULL)\s*\))\s*;/g;
    for(const m of s.masked.matchAll(io))add(m.index,m.index+m[0].length,'标准 I/O 初始化');
    // Strict recogniser for a main dispatcher, not a C++ parser. Any residue
    // (including algorithmic statements) keeps the whole main in the body.
    const main=/\b(?:int|signed)\s+main\s*\(\s*(?:void\s*)?\)\s*\{/g;
    for(const m of s.masked.matchAll(main)) {
      let p=m.index+m[0].length,depth=1;
      while(p<s.masked.length&&depth){if(s.masked[p]==='{')depth++;if(s.masked[p]==='}')depth--;p++;}
      if(depth)continue;
      const original=s.masked.slice(m.index,p);
      if(!/\b(?:run|solve)\s*\(\s*\)\s*;/.test(original))continue;
      let remainder=original.replace(/^.*?\{/s,'').replace(io,'');
      remainder=remainder.replace(/\b(?:int|long\s+long)\s+(?:t|T|tc|tests)\s*(?:=\s*1)?\s*;/g,'')
        .replace(/\bcin\s*>>\s*(?:t|T|tc|tests)\s*;/g,'')
        .replace(/\bwhile\s*\(\s*(?:t|T|tc|tests)\s*--\s*\)/g,'')
        .replace(/\bfor\s*\(\s*int\s+i\s*=\s*0\s*;\s*i\s*<\s*(?:t|T|tc|tests)\s*;\s*(?:i\s*\+\+|\+\+\s*i)\s*\)/g,'')
        .replace(/\b(?:run|solve)\s*\(\s*\)\s*;/g,'').replace(/\breturn\s+0\s*;/g,'').replace(/[{}\s]/g,'');
      if(!remainder)add(m.index,p,'严格匹配的 main 测试分发壳');
    }
    ranges.sort((a,b)=>a.start-b.start);
    return ranges;
  }
  function normalizeTokens(tokens) {
    const ids=new Map();
    return tokens.map(t=>{if(t.kind!=='identifier'||KEYWORDS.has(t.text))return t.text;
      if(!ids.has(t.text))ids.set(t.text,'$id'+ids.size);return ids.get(t.text);});
  }
  function analyze(code) {
    const s=scan(code),r=regions(s),merged=[];
    for(const x of r) {const last=merged[merged.length-1];if(last&&x.start<=last.end)last.end=Math.max(last.end,x.end);else merged.push({start:x.start,end:x.end});}
    let rangeIndex=0;const body=[],template=[];
    for(const t of s.tokens) {while(rangeIndex<merged.length&&merged[rangeIndex].end<=t.start)rangeIndex++;const x=merged[rangeIndex];(x&&t.start>=x.start&&t.end<=x.end?template:body).push(t);}
    const raw=body.map(t=>t.text),normalized=normalizeTokens(body);
    const features=FEATURES.map(f=>({...f,opportunities:0,hits:0,rate:null,evidence:[],evidenceTotal:0}));
    const fmap=Object.fromEntries(features.map(f=>[f.id,f])),lines=s.code.split('\n'),lineStarts=[];let lineOffset=0;
    for(const line of lines){lineStarts.push(lineOffset);lineOffset+=line.length+1;}
    const add=(id,t,match)=>{const f=fmap[id];f.opportunities++;if(match)f.hits++;f.evidenceTotal++;
      if(f.evidence.length<30)f.evidence.push({line:t.line,text:lines[t.line-1].slice(0,400),match:!!match,column:t.start-lineStarts[t.line-1]+1});};
    const tight=(a,b)=>!!a&&!!b&&a.end===b.start;
    for(let i=0;i<body.length;i++) {
      const t=body[i],p=body[i-1],n=body[i+1],v=t.text;
      if(['if','for','while','switch','catch'].includes(v)&&n&&n.text==='(')add('control_tight',t,tight(t,n));
      if(v==='='&&p&&n)add('assignment_tight',t,tight(p,t)&&tight(t,n));
      if(['==','!=','<=','>='].includes(v)&&p&&n)add('comparison_tight',t,tight(p,t)&&tight(t,n));
      if(v===','&&n)add('comma_tight',t,tight(t,n));
      if(v===')'&&n&&n.text==='{')add('paren_brace_tight',t,tight(t,n));
      if(v==='}'&&n&&n.text==='else')add('else_tight',t,tight(t,n));
      if(v==='++'&&(!p||p.text!==')'))add('postfix_increment',t,!!p&&(p.text===']'||(p.kind==='identifier'&&!KEYWORDS.has(p.text))));
      if(['and','or','&&','||'].includes(v))add('logical_word',t,v==='and'||v==='or');
      if(v==='cin'&&n&&n.text==='>>') {
        const before=s.masked.slice(lineStarts[t.line-1],t.start);
        add('cin_declaration_same_line',t,/\b(?:int|itn|ll|bool|char|string|float|double|long\s+long)\s+[^;{}]+;\s*$/.test(before));
      }
      if(v==='cout'&&n&&n.text==='<<')add('cout_one_space',t,s.code.slice(t.end,n.start)===' ');
      if(v==='for'&&n&&n.text==='(') {
        const tail=body.slice(i+2,i+10).map(x=>x.text).join(' '),m=/^(?:int|long long|auto|size_t) [\w$\u0080-\uffff]+ = ([01]) ;/.exec(tail);
        if(m)add('for_zero',t,m[1]==='0');
      }
    }
    const firstByLine=new Map();for(const t of body)if(!firstByLine.has(t.line))firstByLine.set(t.line,t);
    const bodyLines=Array.from(firstByLine.keys());
    for(const line of bodyLines)if(/^[\t ]+\S/.test(lines[line-1]))add('tab_indent',firstByLine.get(line),lines[line-1][0]==='\t');
    features.forEach(f=>f.rate=f.opportunities?f.hits/f.opportunities:null);
    const warnings=s.warnings.slice();
    if(/^\s*#\s*(?:if|ifdef|ifndef|elif|else|endif)\b/m.test(s.masked))warnings.push('存在条件预处理指令；未求值 #if（包括 #if 0），条件分支代码均按文本计入，需人工检查。');
    if(!s.tokens.length)warnings.push('没有可分析代码：输入为空白或仅含注释。');
    else if(!body.length)warnings.push('仅检测到低成本模板，没有可比较的主体。');
    else if(body.length<80)warnings.push('主体少于 80 个 token，描述性比较不稳定，不给综合距离。');
    warnings.push('词法启发式不是完整 C++ 解析器；模板/宏、复杂语法与注释间隔可能需要人工复核。');
    const templateLines=Array.from(new Set(template.map(t=>t.line)));
    return {version:VERSION,summary:{lineCount:s.code.length?lines.length:0,tokenCount:s.tokens.length,bodyTokenCount:body.length,templateTokenCount:s.tokens.length-body.length,bodyLineCount:bodyLines.length,lexicallyComplete:s.warnings.length===0,observableFeatures:features.filter(f=>f.opportunities>0).length},features,regions:{bodyLines,templateLines,templateReasons:Array.from(new Set(r.map(x=>x.reason))),ranges:r},warnings,tokens:{raw,normalized},fingerprint:JSON.stringify(raw)};
  }
  function buildProfile(inputs) {
    if(!Array.isArray(inputs))throw new TypeError('samples must be an array');
    const samples=[],excluded=[],seen=new Map(),seenNormalized=new Map(),warnings=[];
    inputs.forEach((item,index)=>{
      const id=String(item.id==null?index+1:item.id),name=String(item.name||id),problemId=String(item.problemId||item.problem_id||('unknown:'+id));
      const analysis=analyze(item.code),key=analysis.fingerprint,norm=JSON.stringify(analysis.tokens.normalized);
      if(!analysis.summary.bodyTokenCount) {excluded.push({id,name,reason:'无主体代码'});return;}
      if(seen.has(key)) {excluded.push({id,name,reason:'主体 token 完全重复，未重复加权',duplicateOf:seen.get(key)});return;}
      if(analysis.summary.bodyTokenCount>=40&&seenNormalized.has(norm)) {excluded.push({id,name,reason:'标识符规范化后主体完全重复，保守排除克隆加权',duplicateOf:seenNormalized.get(norm)});return;}
      seen.set(key,id);seenNormalized.set(norm,id);
      samples.push({id,name,problemId,provenance:item.provenance||null,analysis});
      if(!item.problemId&&!item.problem_id)warnings.push(name+' 缺少题目 ID，暂按独立题目计；请核对跨文件同题。');
    });
    const problemIds=Array.from(new Set(samples.map(s=>s.problemId)));
    const features=FEATURES.map(f=>{
      const perProblem=problemIds.map(problemId=>{
        const observed=samples.filter(s=>s.problemId===problemId).map(s=>s.analysis.features.find(x=>x.id===f.id)).filter(x=>x.rate!==null);
        return {problemId,rate:mean(observed.map(x=>x.rate)),samples:observed.length,opportunities:observed.reduce((a,x)=>a+x.opportunities,0)};
      }).filter(x=>x.rate!==null);
      return {...f,rate:mean(perProblem.map(x=>x.rate)),problemCount:perProblem.length,opportunities:perProblem.reduce((a,x)=>a+x.opportunities,0),perProblem};
    });
    if(problemIds.length<5)warnings.push('参考画像少于 5 个独立题目，不给综合距离。');
    warnings.push('画像按题目等权；同题多份先取文件率平均，缺失机会不作 0。账号来源不等于独立确认本人撰写。');
    return {version:VERSION,summary:{inputSamples:inputs.length,includedSamples:samples.length,problemCount:problemIds.length,excludedSamples:excluded.length},features,samples,excluded,warnings};
  }
  function ngrams(tokens,n=5) {const out=new Set();for(let i=0;i<=tokens.length-n;i++)out.add(JSON.stringify(tokens.slice(i,i+n)));return out;}
  function jaccard(a,b) {if(!a.size||!b.size)return null;let common=0;a.forEach(x=>{if(b.has(x))common++;});return common/(a.size+b.size-common);}
  function compare(profile,code) {
    if(!profile||!Array.isArray(profile.samples))throw new TypeError('Use buildProfile(samples) first');
    const analysis=analyze(code),raw=ngrams(analysis.tokens.raw),normalized=ngrams(analysis.tokens.normalized);
    const features=analysis.features.map(f=>{const r=profile.features.find(x=>x.id===f.id);const delta=f.rate===null||!r||r.rate===null?null:f.rate-r.rate;
      return {...f,referenceRate:r?r.rate:null,referenceProblems:r?r.problemCount:0,referenceOpportunities:r?r.opportunities:0,delta,absoluteDelta:delta===null?null:Math.abs(delta)};});
    // Only well-observed rows enter the unweighted descriptive L1 distance.
    const eligible=features.filter(f=>f.delta!==null&&f.opportunities>=3&&f.referenceProblems>=3);
    const enough=analysis.summary.lexicallyComplete&&analysis.summary.bodyTokenCount>=80&&profile.summary.problemCount>=5&&eligible.length>=4;
    const distance=enough?100*mean(eligible.map(f=>f.absoluteDelta)):null;
    const similarities=profile.samples.map(s=>{
      const other=s.analysis.tokens;
      return {id:s.id,name:s.name,problemId:s.problemId,rawJaccard:jaccard(raw,ngrams(other.raw)),normalizedJaccard:jaccard(normalized,ngrams(other.normalized)),exactBodyTokens:analysis.summary.bodyTokenCount>0&&analysis.fingerprint===s.analysis.fingerprint,renamedBodyTokens:analysis.summary.bodyTokenCount>0&&JSON.stringify(analysis.tokens.normalized)===JSON.stringify(other.normalized)};
    }).sort((a,b)=>(b.rawJaccard??-1)-(a.rawJaccard??-1));
    const warnings=[...analysis.warnings,...profile.warnings];
    if(!enough)warnings.push('覆盖不足：综合距离需要候选主体 ≥80 token、参考 ≥5 题、至少 4 项各有候选 ≥3 次机会且参考 ≥3 题可观察。');
    if(similarities.some(x=>x.exactBodyTokens))warnings.push('候选与参考主体 token 完全重复；可能存在训练/测试重叠，不能作为独立验证。');
    else if(similarities.some(x=>x.renamedBodyTokens))warnings.push('候选与参考主体经标识符规范化后完全一致；可能是改名版本，不宜作为独立验证。');
    warnings.push('相似度受题意、算法与公共代码影响，不是作者归属证据。距离/相容度未经独立校准，不代表 AI 概率或已知误报率。');
    warnings.push('规范化只按标识符首次出现次序编号，不解析作用域；新增标识符会令后续编号变化。可比特征集合随候选变化，综合距离不用于跨候选排名。');
    return {version:VERSION,analysis,summary:{distance,compatibility:distance===null?null:100-distance,comparableFeatures:eligible.length,status:enough?'descriptive_only':'insufficient',metric:'逐特征绝对率差的等权均值 × 100；仅覆盖充分的特征',ngramSize:5},features,similarities,warnings:Array.from(new Set(warnings))};
  }
  return {VERSION,MAX_CHARS,FEATURES,analyze,buildProfile,compare};
});
