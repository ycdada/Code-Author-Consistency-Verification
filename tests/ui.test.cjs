'use strict';
// Lightweight DOM harness: exercises app.js with the real engine and demo data.
// This is not a substitute for layout, focus, download, or real-browser QA.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const dist = path.join(__dirname, '..', 'dist');
class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.events = {};
    this.value = '';
    this.dataset = {};
    this.classList = {toggle() {}};
    this.files = [];
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(key, value) { this[key] = value; }
  removeAttribute(key) { delete this[key]; }
  addEventListener(name, handler) { this.events[name] = handler; }
  focus() {}
  remove() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() { return this.events.click?.({target: this}); }
}
function setup() {
  const ids = {};
  const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8');
  for (const match of html.matchAll(/id="([^"]+)"/g)) ids[match[1]] = new Element();
  const tabs = ['work', 'references', 'method'].map(name => {
    const element = new Element('button'); element.dataset.tab = name; return element;
  });
  const panels = ['work', 'references', 'method'].map(name => {
    ids['tab-' + name].id = 'tab-' + name; return ids['tab-' + name];
  });
  const downloads = [];
  const context = {
    console, confirm: () => true,
    document: {
      getElementById: id => ids[id], createElement: tag => new Element(tag),
      querySelectorAll: selector => selector === '.tab' ? tabs : panels,
      body: new Element('body'),
    },
    // Delays are deterministic here. Browser timing is tested separately.
    setTimeout: callback => { callback(); return 1; }, clearTimeout() {},
    Blob, Date,
    URL: {createObjectURL(blob) { downloads.push(blob); return 'blob:test'; }, revokeObjectURL() {}},
  };
  context.window = context;
  vm.createContext(context);
  for (const file of ['baseline.js', 'engine.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(dist, file), 'utf8'), context, {filename: file});
  }
  return {ids, context, downloads};
}
function importFile(ids, text) {
  return ids['library-file'].events.change({target: {files: [{size: text.length, text: async () => text}], value: 'selected'}});
}
test('initializes baseline and renders real-engine evidence without undefined fields', () => {
  const {ids, context} = setup();
  assert.equal(ids['ref-count'].textContent, String(context.DEMO_BASELINE.length));
  assert.ok(ids['candidate-code'].value.trim());
  ids.analyze.click();
  assert.ok(ids.results.textContent.includes('核验结果'));
  assert.ok(ids.results.textContent.includes('参考机会数'));
  assert.ok(!ids.results.textContent.includes('undefined'));
});
test('editing candidate invalidates old results and removes report export', () => {
  const {ids} = setup(); ids.analyze.click();
  ids['candidate-code'].value = 'int main(){return 0;}';
  ids['candidate-code'].events.input();
  assert.ok(ids.results.textContent.includes('先前结果已失效'));
  assert.ok(!ids.results.textContent.includes('导出完整报告 JSON'));
});
test('excludes a reference with the same candidate problem ID', () => {
  const {ids, context} = setup();
  ids['candidate-problem'].value = context.DEMO_BASELINE[0].problemId;
  ids.analyze.click();
  const count = context.DEMO_BASELINE.filter(r => r.problemId === ids['candidate-problem'].value).length;
  assert.ok(ids.results.textContent.includes('排除同题 ' + count + ' 份'));
});
test('empty reference library reports insufficient evidence', () => {
  const {ids} = setup(); ids['clear-library'].click();
  assert.equal(ids['ref-count'].textContent, '0');
  ids.analyze.click();
  assert.ok(ids.results.textContent.includes('当前证据不足'));
});
test('reloads baseline and exports a round-trippable library JSON', async () => {
  const {ids, context, downloads} = setup();
  ids['clear-library'].click(); ids['load-baseline'].click(); ids['export-library'].click();
  assert.equal(downloads.length, 1);
  const data = JSON.parse(await downloads[0].text());
  assert.equal(data.samples.length, context.DEMO_BASELINE.length);
  assert.equal(data.samples[0].code, context.DEMO_BASELINE[0].code);
  await importFile(ids, JSON.stringify(data));
  assert.equal(ids['ref-count'].textContent, String(data.samples.length));
});
test('blocks analysis of multiple labeled owners', () => {
  const {ids} = setup(); const rows = ids['reference-list'].children;
  assert.ok(rows.length >= 2);
  for (let index = 0; index < 2; index++) {
    const input = rows[index].children[1].children[0];
    input.value = ['账号甲', '账号乙'][index]; input.events.change();
  }
  ids.analyze.click();
  assert.ok(ids.status.textContent.includes('不同姓名'));
});
test('app has no HTML insertion, persistence, evaluation, or analysis network sinks', () => {
  const source = fs.readFileSync(path.join(dist, 'app.js'), 'utf8');
  assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|localStorage|eval\(|fetch\(/);
});
test('JSON imports malicious sample names as literal text nodes', async () => {
  const {ids} = setup();
  const payload = '<img src=x onerror=alert(1)>';
  await importFile(ids, JSON.stringify({samples: [{name: payload, code: 'int main(){return 0;}', owner: '单一账号', problemId: 'test'}]}));
  assert.equal(ids['ref-count'].textContent, '1');
  assert.ok(ids['reference-list'].textContent.includes(payload));
  const tags = [];
  function visit(element) { tags.push(element.tagName); element.children.forEach(visit); }
  visit(ids['reference-list']);
  assert.ok(!tags.includes('img'));
});
test('invalid JSON leaves the existing library intact', async () => {
  const {ids} = setup(); const count = ids['ref-count'].textContent;
  await importFile(ids, '{bad json');
  assert.ok(ids.status.textContent.includes('导入失败'));
  assert.equal(ids['ref-count'].textContent, count);
});
test('missing candidate problem ID emits an explicit limitation', () => {
  const {ids} = setup(); ids['candidate-problem'].value = ''; ids.analyze.click();
  assert.ok(ids.results.textContent.includes('待测题目标识未填写'));
});
test('candidate above engine character limit is rejected before analysis', () => {
  const {ids, context} = setup();
  ids['candidate-code'].value = 'x'.repeat(context.StyleEngine.MAX_CHARS + 1);
  ids.analyze.click();
  assert.ok(ids.status.textContent.includes('500,000 字符'));
});
