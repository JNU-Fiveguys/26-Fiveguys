"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { CalendarClock, KeyRound, RefreshCw } from "lucide-react";
import { Section } from "@/components/ui/Layout";
import { Chip, StatusBadge } from "@/components/ui/Chip";
import { Banner } from "@/components/ui/Feedback";
import { useToast } from "@/components/ui/Toast";
import { useAppData } from "@/components/app/AppData";
import { EclassSyncBanner, LoginButton } from "./EclassSyncBanner";
import { LoginCredsButton } from "./LoginCreds";
import { api } from "@/lib/api";
import { RUN_SOURCE_LABEL, fmtDuration, runResult, type EclassSource, type RunRecord } from "@/lib/assignments";
import { fmtRelative, fmtShortStamp, fmtTime, parseLocal } from "@/lib/dates";
import { attachmentWarning, hasSyncWarning, redactSyncDetails, syncFailureText } from "@/lib/eclassSync";

// /settings/sources 의 e클래스 행 (F6-S08 · Frontend-Route 11-6) — 주기 · 예약 작업 · 마지막 성공 · 학교 로그인 · 최근 실행.

function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border py-3 last:border-b-0">
      <div className="min-w-[160px] flex-1">
        <p className="text-[14px] font-semibold">{label}</p>
        {hint && <p className="text-[12px] text-muted">{hint}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

function countsText(r: RunRecord): string {
  const c = r.counts ?? {};
  const parts = [c.courses !== undefined && `과목 ${c.courses}`, c.assignments !== undefined && `과제 ${c.assignments}`, c.files ? `파일 ${c.files}` : null, c.posts ? `글 ${c.posts}` : null];
  const l = r.ledger;
  if (l?.new) parts.push(`새 과제 ${l.new}`);
  if (l?.changed) parts.push(`마감 변경 ${l.changed}`);
  return parts.filter(Boolean).join(" · ");
}

function RunLine({ r }: { r: RunRecord }) {
  const res = runResult(r);
  const src = r.source ? RUN_SOURCE_LABEL[r.source] ?? r.source : "";
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1">
      <span className="num w-[92px] text-muted">{fmtShortStamp(r.started_at)}</span>
      <StatusBadge tone={res.tone}>{res.label}</StatusBadge>
      <span className="text-[12px] text-faint">
        {src}
        {(r.attempt ?? 1) > 1 && ` ${(r.attempt ?? 1) - 1}회째`}
        {r.dryRun && " · 미리보기"}
      </span>
      <span className="num text-muted">{fmtDuration(r.duration_s)}</span>
      <span className="min-w-0 flex-1 text-muted">{r.exit_code === 0 ? countsText(r) : r.exit_code === 3 || r.exit_code === null ? "" : syncFailureText(r)}</span>
      {hasSyncWarning(r) && <span className="w-full text-[12px] text-warn-text">{attachmentWarning(r)}</span>}
    </li>
  );
}

export function EclassSourceSection() {
  const toast = useToast();
  const { status, syncing, startSync } = useAppData();
  const [src, setSrc] = useState<EclassSource | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    try {
      setSrc(await api.eclassSource());
      setFailed(null);
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    }
  }, []);
  // 처음 + 실행이 끝나거나 로그인 창 상태가 바뀔 때 다시 (진행 상태 자체는 /api/status 폴링이 본다)
  const stamp = `${status?.sync.running}|${status?.sync.finished_at}|${status?.eclass?.login?.running}|${status?.eclass?.retry?.next_at ?? ""}`;
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load, stamp]);

  const patch = async (body: { intervalHours?: number; scheduled?: boolean }, done: string) => {
    setSaving(true);
    try {
      setSrc(await api.patchEclassSource(body));
      toast(done, { tone: "success" });
    } catch (e) {
      toast(`저장하지 못했습니다: ${e instanceof Error ? e.message : String(e)}`, { tone: "error" });
    } finally {
      setSaving(false);
    }
  };

  const task = src?.task;
  const lastOk = src?.runs.find((r) => r.exit_code === 0);
  const runs = src?.runs ?? [];
  const shownRuns = showAll ? runs : runs.slice(0, 6);
  const login = src?.login;

  return (
    <Section
      title="e클래스 (과제·동영상·공지·강의자료)"
      action={
        <button type="button" className="btn btn-primary btn-sm" onClick={() => void startSync()} disabled={syncing || !!login?.problem}>
          <RefreshCw className={syncing ? "animate-spin" : ""} aria-hidden />
          {syncing ? (status?.sync.source === "external" ? "예약 동기화 진행 중…" : "진행 중…") : "지금 수집"}
        </button>
      }
    >
      <div className="space-y-3">
        <EclassSyncBanner />
        {failed && <Banner tone="danger">수집 원천 정보를 불러오지 못했습니다: {failed}</Banner>}
        {login?.problem && <Banner tone="warn">{login.problem}</Banner>}
      </div>

      <Row
        label="주기"
        hint={src ? `${src.slots.map((s) => s.slice(0, 2)).join("·")}시 · 다음 ${fmtTime(parseLocal(src.nextSlot))} · 놓치면 켜질 때 한 번 따라잡기 · 요청 실패는 5·15·45분 뒤 다시` : "불러오는 중"}
      >
        <select
          className="field field-sm w-auto"
          aria-label="주기"
          value={src?.intervalHours ?? 4}
          disabled={!src || saving}
          onChange={(e) =>
            void patch(
              { intervalHours: Number(e.target.value) },
              task?.registered ? "주기를 바꾸고 예약 실행을 다시 등록했습니다" : "주기를 저장했습니다 — 예약 실행을 켜면 이 주기로 돕니다",
            )
          }
        >
          {(src?.choices ?? [2, 4, 6, 12]).map((h) => (
            <option key={h} value={h}>
              {h}시간{h === 4 ? " (00·04·08·12·16·20시)" : ""}
            </option>
          ))}
        </select>
      </Row>

      <Row
        label={
          <span className="inline-flex items-center gap-1.5">
            <CalendarClock className="size-4 text-faint" aria-hidden />
            예약 실행
          </span>
        }
        hint={
          !task
            ? "확인 중"
            : !task.available
              ? task.error ?? "이 서버에서는 예약 실행을 쓰지 않습니다"
              : task.registered
                ? `${task.pathOk === false ? "옛 폴더를 가리킵니다 — 다시 등록하세요 · " : ""}다음 실행 ${task.nextRun ? fmtShortStamp(task.nextRun) : "-"}${task.lastRun ? ` · 마지막 ${fmtShortStamp(task.lastRun)}` : ""}`
                : `등록되지 않았습니다 — 켜면 PC 가 켜져 있는 동안 창 없이 주기마다 수집합니다${task.legacy?.length ? ` · 예전 작업(${task.legacy.map((l) => l.name).join(", ")})은 등록할 때 지웁니다` : ""}`
        }
      >
        {task?.available &&
          (task.registered ? (
            <>
              <StatusBadge tone={task.pathOk === false ? "warn" : "ok"}>{task.pathOk === false ? "경로 확인" : "켜짐"}</StatusBadge>
              {task.pathOk === false && (
                <button type="button" className="btn btn-sm" disabled={saving} onClick={() => void patch({ scheduled: true }, "예약 실행을 다시 등록했습니다")}>
                  다시 등록
                </button>
              )}
              <button type="button" className="btn btn-ghost btn-sm" disabled={saving} onClick={() => void patch({ scheduled: false }, "예약 실행을 껐습니다")}>
                끄기
              </button>
            </>
          ) : (
            <button type="button" className="btn btn-sm" disabled={saving} onClick={() => void patch({ scheduled: true }, "예약 실행을 켰습니다")}>
              켜기
            </button>
          ))}
      </Row>

      <Row label="마지막 성공">
        <span className="num text-[14px]">
          {lastOk ? `${fmtShortStamp(lastOk.finished_at ?? lastOk.started_at)} (${fmtRelative(lastOk.finished_at ?? lastOk.started_at)}) · ${countsText(lastOk)}` : src ? "아직 없습니다" : "확인 중"}
        </span>
      </Row>

      <Row
        label={
          <span className="inline-flex items-center gap-1.5">
            <KeyRound className="size-4 text-faint" aria-hidden />
            학교 로그인 (C3)
          </span>
        }
        hint={
          login
            ? `${login.hasSession ? `세션 저장 ${login.sessionSavedAt ? fmtRelative(login.sessionSavedAt) : ""}` : "세션 없음"} · ${
                login.hasCreds ? "자동 로그인 정보 저장됨 — 세션이 만료돼도 창 없이 다시 로그인합니다" : "자동 로그인 정보 없음 — 세션이 만료되면 로그인 창이 필요합니다"
              }`
            : "확인 중"
        }
      >
        {login && <Chip square>{login.hasCreds ? "완전 무인" : "반자동"}</Chip>}
        {login && <LoginCredsButton saved={login.hasCreds} onChanged={() => void load()} />}
        <LoginButton />
      </Row>

      <div className="pt-3">
        <p className="label">최근 실행</p>
        {runs.length === 0 ? (
          <p className="text-[13px] text-muted">{src ? "실행 기록이 없습니다" : "불러오는 중…"}</p>
        ) : (
          <ul className="text-[13px]">
            {shownRuns.map((r) => (
              <RunLine key={`${r.started_at}-${r.attempt}`} r={r} />
            ))}
          </ul>
        )}
        {runs.length > 6 && (
          <button type="button" className="mt-1 text-[13px] font-semibold text-primary" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "접기" : `${runs.length - 6}건 더 보기`}
          </button>
        )}
        {src && src.log.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-[13px] font-semibold text-muted">자세히 — sync.log 끝부분</summary>
            <pre className="thin-scroll mt-2 max-h-48 overflow-auto rounded-lg bg-surface-2 p-3 text-[12px] leading-relaxed whitespace-pre-wrap">{src.log.map(redactSyncDetails).join("\n")}</pre>
          </details>
        )}
      </div>
    </Section>
  );
}
