"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight, CalendarPlus, ListTodo, Maximize2, RefreshCw, Send, Sparkles, TriangleAlert } from "lucide-react";
import { useAppData } from "@/components/app/AppData";
import { fmtRelative } from "@/lib/dates";
import type { AcademicStatus } from "@/lib/types";
import { CHAT_EXAMPLES } from "@/lib/demo";
import { navigateQuery } from "@/lib/useQueryState";
import { attachmentWarning, hasSyncWarning, syncFailureText, syncRunResult } from "@/lib/eclassSync";

// 왼쪽 컬럼 — 일정·할 일 등록 / 빠른 실행(원천별 동기화) / 확인 필요 / 대화 카드 (Frontend-Route 6-2 · 11-5 · 14-3).

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-4">
      <h2 className="mb-3 text-[13px] font-bold tracking-wide text-muted">{title}</h2>
      {children}
    </section>
  );
}

function SyncRow({
  label,
  busy,
  busyText,
  meta,
  error,
  errorTone = "danger",
  onClick,
  disabled,
  demo,
}: {
  label: string;
  busy: boolean;
  busyText: string;
  meta: string;
  error?: React.ReactNode;
  errorTone?: "danger" | "warn";
  onClick: () => void;
  disabled?: boolean;
  demo?: boolean;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onClick}
        disabled={busy || disabled}
        className="group flex w-full items-center gap-3 rounded-lg border border-border px-3 py-2.5 text-left transition-colors hover:border-border-strong hover:bg-surface-2 disabled:cursor-not-allowed"
      >
        <RefreshCw className={`size-4 flex-none ${busy ? "animate-spin text-primary" : "text-faint group-hover:text-primary"}`} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] font-semibold">{busy ? busyText : label}</span>
          <span className="block truncate text-[12px] text-faint">
            {meta}
            {demo && " · 예시"}
          </span>
        </span>
      </button>
      {error && <p className={`mt-1.5 flex items-start gap-1.5 px-1 text-[12px] font-medium ${errorTone === "warn" ? "text-warn-text" : "text-danger-text"}`}>{error}</p>}
    </div>
  );
}

/** 학사 원천(학사일정 표·학사공지·학과) 중 가장 최근 성공 시각과 일정 수 — '3분 전 · 160건' (F1-S02) */
function academicMeta(a: AcademicStatus | undefined): string {
  if (!a) return "상태 확인 중";
  if (!a.available) return "F1_Bachelor_agent 를 불러오지 못했습니다";
  if (a.sync?.retry?.next_at) return `수집 실패 — ${a.sync.retry.next_at.slice(11, 16)}에 다시 시도`;
  const on = (a.sources ?? []).filter((s) => s.enabled);
  const last = on.map((s) => s.lastOkAt).filter(Boolean).sort().at(-1) ?? null;
  if (!last) return "아직 수집하지 않았습니다";
  const n = on.reduce((sum, s) => sum + (s.count ?? 0), 0);
  return `${fmtRelative(last)} · ${n}건`;
}

export default function ActionPanel({ reviewCount, onNew }: { reviewCount: number; onNew: (kind: "event" | "todo") => void }) {
  const { status, syncing, error, startSync, academicSyncing, startAcademicSync, startLogin, loginRunning } = useAppData();
  const failed = status && !syncing && status.sync.exit_code !== null && status.sync.exit_code !== 0 && status.sync.exit_code !== 3;
  const partial = !syncing && hasSyncWarning(status?.sync);
  const academic = status?.academic;
  const acFailed = (academic?.sources ?? []).filter((s) => s.enabled && (s.state === "failed" || s.state === "format_changed"));
  // 마지막 학사일정 수집에서 새로 찾아 바로 등록된 일정 (수집 중에는 셈이 바뀌므로 끝난 뒤에 보인다)
  const newCount = academicSyncing ? 0 : (academic?.newCount ?? 0);

  return (
    <div className="flex flex-col gap-4">
      <Block title="등록">
        <div className="grid grid-cols-2 gap-2">
          <button type="button" className="btn btn-sm justify-start" onClick={() => onNew("event")}>
            <CalendarPlus aria-hidden />새 일정
          </button>
          <button type="button" className="btn btn-sm justify-start" onClick={() => onNew("todo")}>
            <ListTodo aria-hidden />새 할 일
          </button>
        </div>
      </Block>

      <Block title="빠른 실행">
        <div className="space-y-2">
          <SyncRow
            label="e클래스 동기화"
            busy={syncing}
            busyText={status?.sync.source === "external" ? "예약 동기화 진행 중…" : "동기화 중…"}
            meta={
              status
                ? status.eclass?.retry
                  ? `${syncRunResult(status.sync).label} · 재시도 대기 중`
                  : `${fmtRelative(status.eclass?.lastOkAt ?? status.updated_at)} · 과제 ${status.counts.deadlines}건`
                : "상태 확인 중"
            }
            disabled={!!error}
            onClick={startSync}
            errorTone={partial ? "warn" : "danger"}
            error={
              failed || partial ? (
                <>
                  <TriangleAlert className="mt-px size-3.5 flex-none" aria-hidden />
                  <span>
                    {partial ? attachmentWarning(status?.sync) : status?.sync.exit_code === 2 ? (
                      <>
                        로그인이 필요합니다 ·{" "}
                        <button type="button" className="underline" onClick={() => void startLogin()} disabled={loginRunning}>
                          {loginRunning ? "로그인 창에서 진행 중…" : "로그인 창 열기"}
                        </button>
                      </>
                    ) : (
                      <>
                        {syncFailureText(status?.sync)} ·{" "}
                        <Link href="/settings/sources" className="underline">
                          수집 원천
                        </Link>
                      </>
                    )}
                  </span>
                </>
              ) : undefined
            }
          />
          <SyncRow
            label="학사일정 동기화"
            busy={academicSyncing}
            busyText={academic?.sync?.source === "external" ? "예약 수집 진행 중…" : "학사일정 수집 중…"}
            meta={academicMeta(academic)}
            disabled={!!error || academic?.available === false}
            onClick={() => void startAcademicSync()}
            error={
              acFailed.length > 0 && !academicSyncing ? (
                <>
                  <TriangleAlert className="mt-px size-3.5 flex-none" aria-hidden />
                  <span>
                    {acFailed.map((s) => s.name).join(", ")} {acFailed.some((s) => s.state === "format_changed") ? "형식이 바뀐 것 같습니다" : "수집 실패"} ·{" "}
                    <Link href="/settings/sources" className="underline">
                      수집 원천
                    </Link>
                  </span>
                </>
              ) : undefined
            }
          />
          <button
            type="button"
            className="flex w-full items-center gap-3 rounded-lg border border-border px-3 py-2.5 text-left transition-colors hover:border-border-strong hover:bg-surface-2"
            onClick={() => navigateQuery({ place: "preview" }, "push")}
          >
            <Sparkles className="size-4 flex-none text-study" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] font-semibold">공강에 배치하기</span>
              <span className="block text-[12px] text-faint">과제·공부를 빈 시간에 · 미리보기 후 등록</span>
            </span>
          </button>
        </div>
      </Block>

      {(reviewCount > 0 || newCount > 0) && (
        // 크기는 예전 '확인 필요' 상자 그대로 — 제목 한 줄 + 설명 한 줄. 숫자마다 가는 곳이 달라 링크를 둘로 나눈다
        <div
          className={`card flex items-center gap-3 p-4 ${reviewCount > 0 ? "border-warn-line bg-warn-soft text-warn-text" : "border-primary-soft-2 bg-primary-soft text-primary"}`}
        >
          {reviewCount > 0 ? <TriangleAlert className="size-5 flex-none" aria-hidden /> : <Sparkles className="size-5 flex-none" aria-hidden />}
          <span className="min-w-0 flex-1">
            <span
              className="block truncate text-[14px] font-bold whitespace-nowrap"
              title={[newCount > 0 && `신규 일정 ${newCount}건`, reviewCount > 0 && `확인 필요 ${reviewCount}건`].filter(Boolean).join(" · ")}
            >
              {newCount > 0 && (
                <Link href="/academic" className="underline-offset-2 hover:underline">
                  신규 일정 {newCount}건
                </Link>
              )}
              {newCount > 0 && reviewCount > 0 && " · "}
              {reviewCount > 0 && (
                <Link href="/academic?tab=review" className="underline-offset-2 hover:underline">
                  확인 필요 {reviewCount}건
                </Link>
              )}
            </span>
            <span className="block truncate text-[12px] opacity-80">
              {newCount > 0 && academic?.lastRunAt ? `${fmtRelative(academic.lastRunAt)} 수집에서 새로 찾은 학사일정` : "공지에서 찾은 학사일정 — 등록 전 확인"}
            </span>
          </span>
          <Link
            href={reviewCount > 0 ? "/academic?tab=review" : "/academic"}
            className="flex-none rounded-sm opacity-90 hover:opacity-100"
            aria-label={reviewCount > 0 ? "확인 필요 보기" : "학사일정 보기"}
          >
            <ArrowRight className="size-4" aria-hidden />
          </Link>
        </div>
      )}

      <ChatCard />
    </div>
  );
}

/** F9 대화 카드 — 모달이 아니라 대시보드에 늘 떠 있다(14-3). 분석 서비스 연결 전이라 입력은 /chat 으로 넘긴다. */
function ChatCard() {
  const router = useRouter();
  const [text, setText] = useState("");
  const go = (q: string) => router.push(`/chat${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[13px] font-bold tracking-wide text-muted">무엇이든 물어보세요</h2>
        <Link href="/chat" className="btn btn-ghost btn-icon btn-sm ml-auto" aria-label="대화 전체 화면">
          <Maximize2 aria-hidden />
        </Link>
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {CHAT_EXAMPLES.map((q) => (
          <button key={q} type="button" className="rounded-full bg-surface-3 px-2.5 py-1 text-[12px] font-medium text-muted transition-colors hover:bg-primary-soft hover:text-primary" onClick={() => go(q)}>
            {q}
          </button>
        ))}
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) go(text.trim());
        }}
      >
        <label htmlFor="chat-quick" className="sr-only">
          질문 또는 일정
        </label>
        <input id="chat-quick" className="field field-sm h-9 flex-1" placeholder="다음 주 목요일 발표 리허설 저녁 8시" value={text} onChange={(e) => setText(e.target.value)} />
        <button type="submit" className="btn btn-primary btn-icon btn-sm size-9" aria-label="보내기" disabled={!text.trim()}>
          <Send aria-hidden />
        </button>
      </form>
    </section>
  );
}
