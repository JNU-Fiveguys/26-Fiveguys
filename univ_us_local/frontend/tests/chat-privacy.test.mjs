import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

// Run the actual component handlers with controlled streaming responses.
const source = readFileSync(new URL('../src/components/pages/ChatPage.tsx', import.meta.url), 'utf8');
const output = ts.transpileModule(source, {compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX,
}}).outputText;

function harness({q = '', settings = async () => ({configured: true, materialsConsent: false})} = {}) {
  let index = 0;
  let captureEffects = true;
  const hooks = [], calls = [], exports = {}, effects = [];
  const api = {
    settings,
    setConsent: async () => ({materials: true}),
    chat(messages, confirmed, on, signal) {
      return new Promise((resolve) => calls.push({messages, confirmed, on, signal, resolve}));
    },
  };
  const react = {
    useState(initial) {
      const slot = index++;
      if (!(slot in hooks)) hooks[slot] = initial;
      return [hooks[slot], (next) => { hooks[slot] = typeof next === 'function' ? next(hooks[slot]) : next; }];
    },
    useRef(initial) {
      const slot = index++;
      if (!(slot in hooks)) hooks[slot] = {current: initial};
      return hooks[slot];
    },
    useCallback(fn) { return fn; },
    useEffect(fn) { if (captureEffects) effects.push(fn); },
  };
  const jsx = (type, props) => ({type, props});
  runInNewContext(output, {exports, AbortController, queueMicrotask, require(name) {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return {jsx, jsxs: jsx};
    if (name === '@/lib/assistant') return {assistantApi: api};
    if (name === '@/lib/useQueryState') return {useQueryValue: () => q};
    return new Proxy({}, {get: (_, key) => String(key)});
  }});
  function render() { index = 0; return exports.default(); }
  function nodes(node, result = []) {
    if (!node || typeof node !== 'object') return result;
    if (Array.isArray(node)) { node.forEach((n) => nodes(n, result)); return result; }
    if (node.props) {
      result.push(node);
      nodes(node.props.children, result);
      nodes(node.props.actions, result);
      nodes(node.props.action, result);
    }
    return result;
  }
  function find(predicate) { return nodes(render()).find(predicate); }
  function send(text) {
    find((n) => n.type === 'textarea').props.onChange({target: {value: text}});
    find((n) => n.type === 'form').props.onSubmit({preventDefault() {}});
  }
  render();
  captureEffects = false;
  let cleanups = effects.map((effect) => effect());
  function unmount() { cleanups.forEach((cleanup) => cleanup?.()); }
  function replayEffects() { unmount(); cleanups = effects.map((effect) => effect()); }
  return {calls, send, find, unmount, replayEffects, settings: () => hooks[3]};
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

test('unapproved private question is blocked from later turns and removed on rejection', async () => {
  const h = harness();
  h.send('전화번호 010-1234-5678');
  h.calls[0].on({event: 'warn', data: {text: '보낼까요?'}});
  h.calls[0].resolve();
  await settle();
  h.send('다음 시험 언제야?');
  assert.equal(h.calls.length, 1);
  assert.equal(h.find((n) => n.type === 'textarea').props.disabled, true);
  const warning = h.find((n) => n.props.m?.warn);
  warning.props.onWarn(false, warning.props.m.warn.question);
  h.send('다음 시험 언제야?');
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].messages.length, 1);
  assert.equal(h.calls[1].messages[0].content, '다음 시험 언제야?');
});

test('explicit approval resends the warned question exactly once', async () => {
  const h = harness();
  h.send('전화번호 010-1234-5678');
  h.calls[0].on({event: 'warn', data: {text: '보낼까요?'}});
  h.calls[0].resolve();
  await settle();
  const warning = h.find((n) => n.props.m?.warn);
  warning.props.onWarn(true, warning.props.m.warn.question);
  assert.equal(h.calls[1].confirmed, true);
  assert.equal(h.calls[1].messages.length, 1);
  assert.equal(h.calls[1].messages[0].content, '전화번호 010-1234-5678');
});

test('clearing a streaming turn aborts it and prevents invisible history from returning', async () => {
  const h = harness();
  h.send('첫 질문');
  h.find((n) => n.type === 'button' && Array.isArray(n.props.children) && n.props.children.includes('대화 지우기')).props.onClick();
  assert.equal(h.calls[0].signal.aborted, true);
  h.calls[0].on({event: 'delta', data: '늦은 답'});
  h.calls[0].resolve();
  await settle();
  h.send('새 질문');
  assert.equal(h.calls[1].messages.length, 1);
  assert.equal(h.calls[1].messages[0].content, '새 질문');
});

test('material consent updates the settings checkbox before retrying', async () => {
  const h = harness();
  await settle();
  h.send('강의자료 설명해 줘');
  h.calls[0].on({event: 'consent', data: {text: '동의 필요'}});
  h.calls[0].resolve();
  await settle();
  const consent = h.find((n) => n.props.m?.consent);
  await consent.props.onConsent(true, consent.props.m.consent.question);
  assert.equal(h.settings().materialsConsent, true);
  assert.equal(h.calls.length, 2);
});

test('StrictMode effect replay preserves the query turn but actual unmount aborts it', async () => {
  const h = harness({q: '다음 시험 언제야?'});
  assert.equal(h.calls.length, 1);
  h.replayEffects();
  await settle();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].signal.aborted, false);
  h.unmount();
  await settle();
  assert.equal(h.calls[0].signal.aborted, true);
});

test('settings button retries a failed initial settings request and opens the recovered form', async () => {
  let attempts = 0;
  const h = harness({settings: async () => {
    if (++attempts === 1) throw new Error('temporary failure');
    return {configured: true, materialsConsent: false, providers: []};
  }});
  await settle();
  assert.ok(h.find((n) => n.type === 'button' && n.props.children === '다시 불러오기'));
  h.find((n) => n.type === 'button' && Array.isArray(n.props.children) && n.props.children.includes('AI 연결 설정')).props.onClick();
  await settle();
  assert.equal(attempts, 2);
  assert.ok(h.find((n) => n.props.value?.configured === true));
  assert.equal(h.find((n) => n.type === 'button' && n.props.children === '다시 불러오기'), undefined);
});
