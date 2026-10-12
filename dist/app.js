(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = {references: [], metadata: {}, report: null, selectedTest: null};
  const MAX_FILE_BYTES = 2 * 1024 * 1024;
  const MAX_CODE = 500000;
  const MAX_TOTAL = 12 * 1024 * 1024;
  let toastTimer;
  function node(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined && text !== null) n.textContent = String(text); if (cls) n.className = cls; return n; }
  function notify(text) { $('status').textContent = text; $('status').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('status').hidden = true; }, 6000); }
  function switchTab(name) { document.querySelectorAll('.tab').forEach(b => { const active = b.dataset.tab === name; b.classList.toggle('active', active); if (active) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); }); document.querySelectorAll('.tab-panel').forEach(p => { p.hidden = p.id !== 'tab-' + name; }); }
  document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  $('manage-refs').addEventListener('click', () => switchTab('references'));
  function validSamples(data) {
    const a = Array.isArray(data) ? data : data.samples || data.references;
    if (!Array.isArray(a) || !a.length || a.length > 200) throw new Error('参考库需包含 1–200 个 samples 数组条目。');
    let total = 0;
    return a.map((r, i) => {
      if (!r || typeof r.code !== 'string' || !r.code.trim()) throw new Error('第 ' + (i + 1) + ' 项缺少非空源码 code。');
      total += r.code.length;
      if (r.code.length > MAX_CODE || total > MAX_TOTAL) throw new Error('源码超过大小限制：单份 500,000 字符，总计 12,582,912 字符。');
      const name = String(r.name || r.filename || '参考 ' + (i + 1)).slice(0, 200);
      return {id: String(r.id || 'ref-' + Date.now() + '-' + i).slice(0, 200), name, owner: String(r.owner || r.account || '').slice(0, 120), code: r.code, problemId: String(r.problemId || name).slice(0, 200), provenance: r.provenance || '导入来源，尚未独立核实'};
    });
  }
  function sourceText(value) { if (typeof value === 'string') return value; if (value && typeof value === 'object') return Object.entries(value).map(([k,v]) => k + '：' + (typeof v === 'string' ? v : JSON.stringify(v))).join('；'); return '来源未核实'; }
  function invalidate() { if (state.report) { $('results').replaceChildren(node('h2', '输入已改变，请重新核验'), node('p', '先前结果已失效。重新分析后才能导出最新报告。', 'muted')); state.report = null; } }
  function updateRefs() {
    const count = state.references.length, problems = new Set(state.references.map(r => r.problemId.trim()).filter(Boolean)).size;
    $('ref-count').textContent = String(count);
    $('baseline-summary').replaceChildren(node('strong', count + ' 份 / ' + problems + ' 题'), node('p', state.metadata.title || state.metadata.name || (count ? '自定义参考库 · 来源待人工核实' : '尚未载入参考')));
    $('baseline-summary').append(node('p', state.metadata.description || state.metadata.provenance || '账号来源不等于手写真值。'));
    $('library-stats').textContent = count + ' 份参考 · ' + problems + ' 个题目标识 · 当前会话内存存储';
    $('export-library').disabled = !count; $('clear-library').disabled = !count;
    const list = $('reference-list'); list.replaceChildren();
    if (!count) { list.append(node('p', '参考库为空。可批量导入源码，或载入工作台的合成示例。', 'muted')); return; }
    state.references.forEach((r, index) => {
      const row = node('div', null, 'ref-row');
      const title = node('div'); title.append(node('strong', r.name), node('span', sourceText(r.provenance), 'fine')); row.append(title);
      const accountLabel = node('label', '姓名 / 账号'); const account = node('input'); account.value = r.owner || ''; account.placeholder = '待核实'; account.maxLength = 120; account.setAttribute('aria-label', r.name + ' 的姓名或账号'); account.addEventListener('change', () => { r.owner = account.value.trim(); invalidate(); }); accountLabel.append(account); row.append(accountLabel);
      const problemLabel = node('label', '题目标识（可编辑）'); const problem = node('input'); problem.value = r.problemId; problem.maxLength = 120; problem.setAttribute('aria-label', r.name + ' 的题目标识'); problem.addEventListener('change', () => { r.problemId = problem.value.trim(); invalidate(); updateRefs(); }); problemLabel.append(problem); row.append(problemLabel);
      const actions = node('div', null, 'ref-actions'); const view = node('button', '查看', 'text-button'); view.addEventListener('click', () => { $('dialog-title').textContent = r.name; $('dialog-code').textContent = r.code; $('code-dialog').showModal(); }); const remove = node('button', '移除', 'text-button danger'); remove.setAttribute('aria-label', '移除 ' + r.name); remove.addEventListener('click', () => { state.references.splice(index, 1); invalidate(); updateRefs(); }); actions.append(view, remove); row.append(actions); list.append(row);
    });
  }
  function loadBaseline(ask) { try { const samples = validSamples(window.DEMO_BASELINE || []); if (ask && state.references.length && !window.confirm('载入示例将替换当前参考库。尚未导出的修改将丢失。继续吗？')) return; state.references = samples; state.metadata = Object.assign({}, window.DEMO_BASELINE_META || {title:'合成示例参考库', description:'合成演示数据，非用户真实 20 题。'}); invalidate(); updateRefs(); if (ask) notify('已载入内置示例。真实账号基线可在参考库页导入 JSON。'); } catch (e) { notify(e.message); } }
  $('load-baseline').addEventListener('click', () => loadBaseline(true));
  $('clear-library').addEventListener('click', () => { if (!window.confirm('清空本次会话的全部参考源码？此操作无法撤销，建议先导出参考库 JSON。')) return; state.references = []; state.metadata = {}; invalidate(); updateRefs(); notify('已清空参考库。'); });
  $('close-dialog').addEventListener('click', () => $('code-dialog').close());
  $('reference-files').addEventListener('change', async (event) => {
    const files = Array.from(event.target.files || []); event.target.value = ''; if (!files.length) return;
    const owner = $('owner-name').value.trim(); if (!owner) { notify('请先填写这批参考所属姓名 / 账号，再选择文件。'); $('owner-name').focus(); return; }
    try { if (state.references.length + files.length > 200) throw new Error('参考样本最多 200 份。'); const texts = []; let total = state.references.reduce((s,r) => s + r.code.length, 0);
      for (const f of files) { if (f.size > MAX_FILE_BYTES) throw new Error(f.name + ' 超过单文件 2 MB 限制。'); const code = await f.text(); if (code.length > MAX_CODE) throw new Error(f.name + ' 超过 500,000 字符限制。'); total += code.length; if (total > MAX_TOTAL) throw new Error('源码总量超过 12 MB 限制。'); if (!code.trim()) throw new Error(f.name + ' 是空文件。'); texts.push({id:'upload-' + Date.now() + '-' + texts.length, name:f.name, code, problemId:f.name.replace(/\.(cpp|cc|cxx|hpp|h|txt)$/i, ''), owner, provenance:'用户导入 · ' + owner + ' · 手写情况未核实'}); }
      state.references.push(...texts); state.metadata = {title:'自定义参考库', description:'请核实来源；逐项检查并编辑题目标识，避免同题不同版本重复计数。'}; invalidate(); updateRefs(); notify('已导入 ' + texts.length + ' 份参考；请核对题目标识。');
    } catch (e) { notify(e.message); }
  });
  $('library-file').addEventListener('change', async (event) => { const f = event.target.files[0]; event.target.value = ''; if (!f) return; try { if (f.size > MAX_TOTAL * 2) throw new Error('JSON 文件过大。'); const data = JSON.parse(await f.text()); const refs = validSamples(data); if (state.references.length && !window.confirm('导入的 ' + refs.length + ' 份参考将替换当前参考库。继续吗？')) return; state.references = refs; state.metadata = data.metadata && typeof data.metadata === 'object' ? data.metadata : {title:'导入参考库', description:'来源需人工核实。'}; invalidate(); updateRefs(); notify('参考库已导入，本地内存中共 ' + refs.length + ' 份。'); } catch (e) { notify('导入失败：' + e.message); } });
  function download(data, name) { const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json;charset=utf-8'}); const url = URL.createObjectURL(blob); const a = node('a'); a.href = url; a.download = name; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 10000); }
  $('export-library').addEventListener('click', () => { download({schemaVersion:'1.0', metadata:state.metadata, samples:state.references}, '墨迹-参考库.json'); notify('已发起参考库下载。文件包含源码，请妥善保管。'); });
  function updateCodeStats() { const c = $('candidate-code').value; $('code-stats').textContent = (c ? c.split('\n').length : 0) + ' 行 · ' + c.length + ' 字符'; }
  function clearTestSelection() { state.selectedTest = null; $('test-select').value = ''; $('test-label').hidden = true; $('reveal-label').disabled = true; }
  $('candidate-code').addEventListener('input', () => { updateCodeStats(); invalidate(); clearTestSelection(); });
  ['candidate-name','candidate-problem'].forEach(id => $(id).addEventListener('input', invalidate));
  $('candidate-file').addEventListener('change', async (event) => { const f = event.target.files[0]; event.target.value = ''; if (!f) return; try { if (f.size > MAX_FILE_BYTES) throw new Error('待测文件超过 2 MB 限制。'); const loadedCode = await f.text(); if (loadedCode.length > MAX_CODE) throw new Error('待测代码超过 500,000 字符限制。'); $('candidate-code').value = loadedCode; $('candidate-name').value = f.name; $('candidate-problem').value = f.name.replace(/\.(cpp|cc|cxx|hpp|h|txt)$/i, ''); clearTestSelection(); updateCodeStats(); invalidate(); notify('待测代码已导入。请检查题目标识。'); } catch (e) { notify(e.message); } });
  function tests() { const t = window.DEMO_TESTS; return Array.isArray(t) ? t : t && Array.isArray(t.samples) ? t.samples : []; }
  function useTest(index) { const t = tests()[index]; if (!t || typeof t.code !== 'string') return; state.selectedTest = t; $('candidate-code').value = t.code; $('candidate-name').value = t.name || '示例样本 ' + (index + 1); $('candidate-problem').value = t.problemId || ''; $('test-label').hidden = true; $('reveal-label').disabled = false; updateCodeStats(); invalidate(); }
  $('test-select').addEventListener('change', () => { if ($('test-select').value === '') { clearTestSelection(); return; } useTest(Number($('test-select').value)); });
  $('reveal-label').addEventListener('click', () => { const t = state.selectedTest; if (!t) return; $('test-label').textContent = '样本来源标签：' + sourceText(t.label || t.provenance || '合成演示样本，非人 / AI 真值') + '。此标签不参与分析，已知回归样本不能用来估计泛化准确率。'; $('test-label').hidden = false; });
  function percent(v) { return typeof v === 'number' && Number.isFinite(v) ? (v * 100).toFixed(1) + '%' : '无有效机会'; }
  function num(v) { return typeof v === 'number' && Number.isFinite(v) ? String(v) : '—'; }
  function featureState(f) { if (f.rate == null || f.referenceRate == null || !f.opportunities || !f.referenceProblems) return '不可比较'; if (f.opportunities < 3 || f.referenceProblems < 3) return '机会不足'; const d = Math.abs(f.delta == null ? f.rate - f.referenceRate : f.delta); return d >= .35 ? '差异较大' : d >= .15 ? '存在差异' : '接近参考'; }
  function renderReport(report) {
    const {result, profile, excludedSameProblem} = report; const root = $('results'); root.className = 'card'; root.replaceChildren();
    const head = node('div', null, 'section-heading'); const heading = node('div'); heading.append(node('p', 'REVIEW EVIDENCE / 核验结果', 'eyebrow'), node('h2', result.summary.status === 'insufficient' ? '当前证据不足，无法判定' : '风格证据已提取，需人工解释')); const exportButton = node('button', '导出完整报告 JSON', 'button secondary'); exportButton.addEventListener('click', () => { download(state.report, '墨迹-核验报告.json'); notify('报告下载已发起。报告包含源码及证据，请妥善保管。'); }); head.append(heading, exportButton); root.append(head);
    root.append(node('span', '未校准 · 不推断作者 / 人或 AI', 'status-pill warning'));
    const metrics = node('div', null, 'metrics'); [['有效参考题目', profile.summary.problemCount], ['可比较特征', result.summary.comparableFeatures], ['待测主体行数', result.analysis.summary.bodyLineCount]].forEach(([label,value]) => { const m = node('div', null, 'metric'); m.append(node('strong', num(value)), node('span', label)); metrics.append(m); }); root.append(metrics);
    root.append(node('p', '输入参考 ' + report.input.references.length + ' 份；排除同题 ' + excludedSameProblem.length + ' 份；去重后纳入 ' + num(profile.summary.includedSamples) + ' 份。来源：' + (state.metadata.title || state.metadata.name || '自定义参考库') + '。账号来源不等于手写真值。', 'muted'));
    const valid = result.features.filter(f => f.rate != null && f.referenceRate != null && f.opportunities >= 3 && f.referenceProblems >= 3); const large = valid.filter(f => featureState(f) === '差异较大').length;
    root.append(node('div', result.summary.status === 'insufficient' ? '不可判定：参考规模或可用特征不足。请补充独立题目、核对样本来源，并查看下列警告。' : '相容性证据：' + valid.length + ' 项可比较习惯中，' + large + ' 项与参考均值相差至少 35 个百分点。这是描述性差异，不能转化为作者、AI 或违规概率。', 'notice'));
    root.append(node('h3', '逐项习惯对照'));
    root.append(node('p', '数值为该习惯在有效机会中的出现率；参考值按题目汇总。待测至少 3 次机会且参考至少 3 题才显示差异标签。差异标签使用固定描述阈值（15 / 35 个百分点），未做统计显著性或概率校准。点击下方源码证据可核查具体行。', 'fine'));
    const wrap = node('div', null, 'table-wrap'); const table = node('table'); const thead = node('thead'), hr = node('tr'); ['特征','参考出现率','待测出现率','待测机会数','参考机会数','参考题目数','对照'].forEach(t => hr.append(node('th',t))); thead.append(hr); table.append(thead); const tbody = node('tbody');
    result.features.forEach(f => { const tr = node('tr'); [f.label,percent(f.referenceRate),percent(f.rate),num(f.opportunities),num(f.referenceOpportunities),num(f.referenceProblems),featureState(f)].forEach((v,i) => tr.append(node('td',v,i === 6 ? 'evidence-tag' + (featureState(f) === '差异较大' ? ' warn' : '') : ''))); tbody.append(tr); }); table.append(tbody); wrap.append(table); root.append(wrap);
    root.append(node('h3', '源码证据：机会与命中行'));
    result.features.forEach(f => { const d = node('details'); d.append(node('summary', f.label + ' · ' + num(f.opportunities) + ' 次机会')); const evidence = Array.isArray(f.evidence) ? f.evidence : []; d.append(node('pre', evidence.length ? evidence.map(e => '第 ' + e.line + ' 行 [' + (e.match ? '命中' : '未命中') + '] ' + e.text).join('\n') : '未发现有效机会，或该特征没有可展示的源码行。', 'detail-pre')); root.append(d); });
    root.append(node('h3', '文本相似度（不等同作者一致性）'));
    root.append(node('p', '原文与规范化相似度为主体词法片段 Jaccard 重叠率。规范化按标识符首次出现顺序编号，保留字面量，不解析作用域；共同算法、题型及模板均可能产生高重叠。百分比是文本重叠比例，不是置信概率。', 'fine'));
    const simWrap = node('div', null, 'table-wrap'), simTable = node('table'), simHead = node('tr'); ['参考样本','题目标识','原文重叠','规范化重叠'].forEach(t => simHead.append(node('th',t))); const simThead = node('thead'); simThead.append(simHead); simTable.append(simThead); const simBody = node('tbody'); (result.similarities || []).forEach(s => { const tr = node('tr'); [s.name || s.id,s.problemId || '未标注',percent(s.rawJaccard),percent(s.normalizedJaccard)].forEach(v => tr.append(node('td',v))); simBody.append(tr); }); simTable.append(simBody); simWrap.append(simTable); root.append(simWrap);
    root.append(node('h3', '风险、来源与不可判定说明'));
    const warnings = [...new Set([...(profile.warnings || []),...(result.warnings || []),...(result.analysis.warnings || []),...(excludedSameProblem.length ? ['已从风格参考与相似度计算中排除同题样本。'] : []),'题目、公共模板、格式化工具与学习变化均可能解释风格差异。','参考来源未经独立确认为手写，不能作为处分依据。'])]; const ul = node('ul', null, 'risk-list'); warnings.forEach(w => ul.append(node('li', sourceText(w)))); root.append(ul);
    const detail = node('details'); detail.append(node('summary','分析审计：模板分区、去重与计算参数')); detail.append(node('pre',JSON.stringify({engineVersion:window.StyleEngine.VERSION || window.StyleEngine.version || '见引擎源码',summary:result.analysis.summary,regions:result.analysis.regions,profileExclusions:profile.excluded,sameProblemExcluded:excludedSameProblem.map(r => ({id:r.id,name:r.name,problemId:r.problemId})),metadata:state.metadata},null,2),'detail-pre')); root.append(detail);
  }
  $('analyze').addEventListener('click', () => {
    const code = $('candidate-code').value;
    const owners = new Set(state.references.map(r => r.owner.trim()).filter(Boolean));
    if (owners.size > 1) { notify('参考库包含不同姓名 / 账号。请只保留同一人的参考，或先更正账号标注再分析。'); switchTab('references'); return; }
    if (!code.trim()) { notify('请先粘贴或导入待测代码。'); $('candidate-code').focus(); return; }
    if (code.length > MAX_CODE) { notify('待测代码超过 500,000 字符限制。'); return; }
    if (!window.StyleEngine) { notify('分析引擎未载入。请确认离线包中 engine.js 与本页位于同一目录。'); return; }
    const btn = $('analyze'); btn.disabled = true; btn.textContent = '正在分析…';
    setTimeout(() => { try {
      const problemId = $('candidate-problem').value.trim(); const excludedSameProblem = problemId ? state.references.filter(r => r.problemId.trim() === problemId) : [];
      const filtered = state.references.filter(r => !excludedSameProblem.includes(r)); const profile = window.StyleEngine.buildProfile(filtered); const result = window.StyleEngine.compare(profile,code);
      if (!problemId) result.warnings.push('待测题目标识未填写，无法自动排除同题参考；请人工核查后补填。');
      if (state.references.some(r => !r.owner.trim())) result.warnings.push('部分参考未标注姓名 / 账号，请确认参考均来自同一人；标注缺失不是作者一致的证据。');
      state.report = {schemaVersion:'1.0',generatedAt:new Date().toISOString(),purpose:'描述性代码风格人工复核；未经校准，不用于作者、人/AI或违规判定',input:{references:state.references.map(r => ({...r})),candidate:{name:$('candidate-name').value,problemId,code}},metadata:{...state.metadata},excludedSameProblem,profile,result}; renderReport(state.report); notify('核验完成。请结合机会数、源码证据与风险说明阅读。');
    } catch (e) { notify('分析失败：' + e.message); } finally { btn.disabled = false; btn.replaceChildren(node('span','开始核验'),node('span',' ↗')); } }, 30);
  });
  tests().forEach((t,i) => { const option = node('option', t.name || '示例样本 ' + (i + 1)); option.value = String(i); $('test-select').append(option); });
  if (Array.isArray(window.DEMO_BASELINE) && window.DEMO_BASELINE.length) loadBaseline(false); else updateRefs();
  if (tests().length) { $('test-select').value = '0'; useTest(0); }
  updateCodeStats();
})();
