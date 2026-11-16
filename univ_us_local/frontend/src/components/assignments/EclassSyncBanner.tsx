"use client";

import Link from "next/link";
import { LogIn, RefreshCw, TriangleAlert } from "lucide-react";
import { SyncBanner, type SyncTone } from "@/components/ui/Feedback";
import { useAppData } from "@/components/app/AppData";
import { fmtRelative, fmtTime, parseLocal } from "@/lib/dates";
import { attachmentWarning, hasSyncWarning, syncFailureText } from "@/lib/eclassSync";

// e클래스 수집 상태 띠 — 실패를 숨기지 않는다 (Frontend-Route 11-7, F6-R15·S09·S10).
//   수집 중 · 재시도 중 → 회색 / 로그인 필요 → 노랑 + '로그인 창 열기' / 연속 실패·실패 → 빨강 + 마지막 성공 · 사유 + '지금 수집'

export function useEclassSyncTone(): SyncTone {
  const { status, syncing } = useAppData();
  const ec = status?.eclass;
  const exit = status?.sync.exit_code;
  if (syncing) return "running";
  if (hasSyncWarning(status?.sync)) return "partial";
  if (ec?.retry) return "retry";
  if (ec?.needLogin || exit === 2) return "login";
  if (ec?.warn || (exit !== null && exit !== undefined && exit !== 0 && exit !== 3)) return "failed";
  return "ok";
}

export function LoginButton({ className = "btn btn-sm" }: { className?: string }) {
  const { startLogin, loginRunning } = useAppData();
  return (
    <button type="button" className={className} onClick={() => void startLogin()} disabled={loginRunning}>
      {loginRunning ? <span className="spin spin-dark" aria-hidden /> : <LogIn aria-hidden />}
      {loginRunning ? "로그인 창에서 진행 중…" : "로그인 창 열기"}
    </button>
  );
}

export function EclassSyncBanner() {
  const { status, startSync } = useAppData();
  const tone = useEclassSyncTone();
  const ec = status?.eclass;
  const last = ec?.lastOkAt ?? status?.updated_at;
  const lastText = last ? `마지막 성공 ${fmtRelative(last)}` : "아직 성공한 수집이 없습니다";
  if (tone === "ok") return null;
  let text: string;
  if (tone === "running") text = status?.sync.source === "external" ? "예약 동기화 진행 중… 목록은 이전 데이터 그대로입니다" : "e클래스 수집 중… 목록은 이전 데이터 그대로입니다";
  else if (tone === "partial") text = `${attachmentWarning(status?.sync)} · ${lastText}`;
  else if (tone === "retry") text = `${syncFailureText(status?.sync)} 다음 자동 시도 ${ec?.retry ? fmtTime(parseLocal(ec.retry.next_at)) : "곧"} · ${lastText}`;
  else if (tone === "login") text = `${syncFailureText({exit_code: 2})} · ${lastText}`;
  else if (ec?.warn) text = `e클래스 수집이 ${ec.failureStreak}회 연속 실패했습니다. ${syncFailureText(status?.sync)} · ${lastText}`;
  else text = `${syncFailureText(status?.sync)} · ${lastText}`;
  return (
    <SyncBanner
      state={tone}
      action={
        tone === "login" ? (
          <LoginButton />
        ) : tone === "failed" || tone === "retry" || tone === "partial" ? (
          <button type="button" className="btn btn-sm" onClick={() => void startSync()}>
            <RefreshCw aria-hidden />
            지금 수집
          </button>
        ) : undefined
      }
    >
      {text}
    </SyncBanner>
  );
}

/** 헤더 아래 전역 띠 — 연속 3회 실패일 때만 (F6-R15 · S09). 평소엔 눈에 띄지 않게. */
export function EclassFailureStrip() {
  const { status } = useAppData();
  const ec = status?.eclass;
  if (!ec?.warn || hasSyncWarning(status?.sync)) return null;
  const last = ec.lastOkAt ?? status?.updated_at;
  return (
    <div role="alert" className="mx-auto mt-2 flex max-w-[1560px] flex-wrap items-center justify-center gap-x-2 gap-y-1 rounded-xl border px-4 py-2 text-center text-[13px] font-semibold text-danger-text"
      style={{ background: "var(--danger-soft)", borderColor: "color-mix(in oklab, var(--danger) 35%, transparent)" }}>
      <TriangleAlert className="size-4 flex-none" aria-hidden />
      e클래스 수집이 {ec.failureStreak}회 연속 실패했습니다 · 마지막 성공 {last ? fmtRelative(last) : "없음"}
      {ec.needLogin && " — 로그인이 필요할 수 있습니다"}
      <Link href="/settings/sources" className="rounded bg-surface px-1.5 underline-offset-2 hover:underline">
        수집 원천
      </Link>
    </div>
  );
}
