import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';
import ts from 'typescript';

// Exercise the pure presentation contract using the project's TypeScript compiler.
const source = readFileSync(new URL('../src/lib/eclassSync.ts', import.meta.url), 'utf8');
const output = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const api = {};
runInNewContext(output, {exports: api});

test('saved coursework remains successful while attachments wait for retry', () => {
  const state = {exit_code: 0, partial: true, counts: {attachmentFailures: 1, attachmentPending: 3}, ledger: {new: 2, changed: 0, removed: 0, submitted: 1}};
  const result = api.syncResultMessage(state);
  assert.equal(result.ok, true);
  assert.equal(result.partial, true);
  assert.match(result.text, /과목·과제 정보는 저장했습니다/);
  assert.match(result.text, /첨부파일 3건은 다음 동기화/);
  assert.match(result.text, /새 과제 2건/);
  assert.equal(api.syncRunResult(state).tone, 'warn');
});

test('pending attachments remain visible on a later run with no new failures', () => {
  assert.equal(api.hasSyncWarning({exit_code: 0, counts: {attachmentFailures: 0, attachmentPending: 2}}), true);
  assert.equal(api.hasSyncWarning({exit_code: 0, partial: false, counts: {attachmentFailures: 0, attachmentPending: 0}}), false);
});

test('fatal collection errors cannot be presented as successful attachment warnings', () => {
  const state = {exit_code: 4, errorKind: 'timeout', partial: true, counts: {attachmentPending: 3}};
  assert.equal(api.hasSyncWarning(state), false);
  assert.equal(api.syncResultMessage(state).ok, false);
  assert.equal(api.syncRunResult(state).label, '요청 시간 초과');
});

test('request timeout, connection failure, login and overall timeout remain distinct', () => {
  assert.match(api.syncFailureText({exit_code: 4, errorKind: 'timeout'}), /요청 시간이 초과/);
  assert.match(api.syncFailureText({exit_code: 4, errorKind: 'network'}), /서버 연결 요청에 실패/);
  assert.match(api.syncFailureText({exit_code: 2}), /로그인이 만료되었거나 필요/);
  assert.match(api.syncFailureText({exit_code: -1}), /수집 시간이 초과/);
});

test('legacy exit 4 does not claim that the internet is disconnected', () => {
  const text = api.syncResultMessage(4).text;
  assert.match(text, /요청에 실패/);
  assert.doesNotMatch(text, /인터넷|네트워크 오류|연결되지/);
  assert.equal(api.syncRunResult(4).label, '요청 실패');
});

test('raw private errors and warning URLs never reach result messages', () => {
  const secret = 'https://example.invalid/private/file?token=secret';
  assert.doesNotMatch(api.syncResultMessage({exit_code: 1, error: secret}).text, /example|secret|private/);
  assert.doesNotMatch(api.syncResultMessage({exit_code: 0, partial: true, warnings: [secret]}).text, /example|secret|private/);
});

test('diagnostic details redact absolute, scheme-relative and relative request URLs', () => {
  const text = api.redactSyncDetails('Timeout GET https://example.invalid/download?id=private url="/download/private?token=hidden" //example.invalid/private');
  assert.match(text, /Timeout GET/);
  assert.doesNotMatch(text, /example|private|token|hidden/);
  assert.match(text, /요청 주소 생략/);
  assert.match(text, /요청 경로 생략/);
});

test('complete success, skipped runs and pending runs keep existing semantics', () => {
  assert.equal(api.syncResultMessage(0).ok, true);
  assert.equal(api.syncResultMessage(0).partial, false);
  assert.equal(api.syncResultMessage(3).ok, true);
  assert.equal(api.syncRunResult(null).label, '진행 중');
  assert.equal(api.syncRunResult(undefined).label, '진행 중');
});
