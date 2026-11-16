"use client";

import type { ReactNode } from "react";
import { FlaskConical, Info, LoaderCircle, OctagonAlert, RefreshCw, TriangleAlert } from "lucide-react";

// 상태 부품 — 배너 · 동기화 상태 띠 · 빈 화면 · 스켈레톤 · 패널 에러.
// 원칙: 실패를 숨기지 않는다(③), 모르는 것은 모른다고 쓴다(⑤) — Frontend-Screens 5절.

type BannerTone = "info" | "warn" | "danger" | "neutral" | "primary";

const BANNER: Record<BannerTone, { cls: string; icon: ReactNode }> = {
  info: { cls: "bg-info-soft text-info-text border-info-line", icon: <Info aria-hidden /> },
  warn: { cls: "bg-warn-soft text-warn-text border-warn-line", icon: <TriangleAlert aria-hidden /> },
  danger: { cls: "bg-danger-soft text-danger-text border-danger-line", icon: <OctagonAlert aria-hidden /> },
  neutral: { cls: "bg-surface-2 text-muted border-border", icon: <Info aria-hidden /> },
  primary: { cls: "bg-primary-soft text-primary border-primary-soft-2", icon: <Info aria-hidden /> },
};

export function Banner({
  tone = "info",
  icon,
  children,
  action,
  className = "",
}: {
  tone?: BannerTone;
  icon?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const b = BANNER[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : undefined}
      className={`flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border px-4 py-3 text-[14px] font-medium ${b.cls} ${className}`}
    >
      <span className="flex min-w-0 flex-1 items-start gap-2.5 [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:flex-none">
        {icon ?? b.icon}
        <span className="min-w-0">{children}</span>
      </span>
      {action && <span className="flex flex-none items-center gap-2">{action}</span>}
    </div>
  );
}

/** 예시 데이터 표시 — 백엔드 API 가 아직 없는 화면 */
export function DemoNotice({ what = "이 화면" }: { what?: string }) {
  return (
    <p className="flex items-center gap-2 rounded-lg border border-dashed border-border-strong px-3 py-2 text-[13px] text-muted">
      <FlaskConical className="size-4 flex-none text-faint" aria-hidden />
      <span>
        <b className="font-semibold text-text">예시 데이터</b> — {what}의 백엔드 API 가 아직 없어 화면 확인용 값으로 보여 줍니다.
      </span>
    </p>
  );
}

export type SyncTone = "ok" | "running" | "retry" | "login" | "partial" | "failed";

/** 동기화 상태 띠 — 정상(숨김) / 진행 중 / 로그인·일부 확인(노랑) / 실패(빨강) */
export function SyncBanner({ state, children, action }: { state: SyncTone; children: ReactNode; action?: ReactNode }) {
  if (state === "ok") return null;
  if (state === "running" || state === "retry")
    return (
      <Banner tone="neutral" icon={<LoaderCircle className="animate-spin" aria-hidden />} action={action}>
        {children}
      </Banner>
    );
  return (
    <Banner tone={state === "login" || state === "partial" ? "warn" : "danger"} action={action}>
      {children}
    </Banner>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  compact,
}: {
  icon?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-xl border border-dashed border-border-strong text-center ${
        compact ? "gap-2 px-4 py-6" : "gap-3 px-6 py-12"
      }`}
    >
      {icon && (
        <span className={`grid place-items-center rounded-full bg-surface-3 text-faint ${compact ? "size-9 [&>svg]:size-4" : "size-12 [&>svg]:size-5"}`}>
          {icon}
        </span>
      )}
      <p className={`font-semibold text-text ${compact ? "text-[14px]" : "text-[15px]"}`}>{title}</p>
      {children && <p className="max-w-md text-[13px] text-muted">{children}</p>}
      {action && <div className="mt-1 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

/** 패널 하나만 실패 — 다른 패널은 정상 동작(19절 6번) */
export function ErrorPanel({ message = "불러오지 못했습니다", onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-danger-line bg-danger-soft px-6 py-8 text-center">
      <OctagonAlert className="size-5 text-danger-text" aria-hidden />
      <p className="text-[14px] font-semibold text-danger-text">{message}</p>
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          <RefreshCw aria-hidden />
          다시 시도
        </button>
      )}
    </div>
  );
}

export function SkeletonList({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="불러오는 중">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3">
          <div className="skeleton size-8 rounded-full" />
          <div className="min-w-0 flex-1 space-y-2">
            <div className="skeleton h-3.5" style={{ width: `${60 - (i % 3) * 12}%` }} />
            <div className="skeleton h-3 w-1/3" />
          </div>
          <div className="skeleton h-6 w-12 rounded-full" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonCards({ count = 3, className = "" }: { count?: number; className?: string }) {
  return (
    <div className={`grid gap-3 ${className}`} aria-busy="true" aria-label="불러오는 중">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="card space-y-3 p-4">
          <div className="skeleton h-4 w-2/5" />
          <div className="skeleton h-3 w-4/5" />
          <div className="skeleton h-3 w-3/5" />
        </div>
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-[13px] text-muted">
      <span className="spin spin-dark" aria-hidden />
      {label}
    </span>
  );
}
