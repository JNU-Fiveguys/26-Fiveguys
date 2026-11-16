import type { SyncState } from "./types";

export type SyncResult = Pick<SyncState, "exit_code"> & Partial<Pick<SyncState,
  "ledger" | "counts" | "partial" | "warnings" | "error" | "errorKind">>;

type ResultInput = SyncResult | number | null | undefined;
const resultOf = (value: ResultInput): SyncResult =>
  typeof value === "object" && value !== null ? value : { exit_code: value ?? null };

/** A saved course/task snapshot can still have attachments waiting to be retried. */
export function hasSyncWarning(value: ResultInput): boolean {
  const result = resultOf(value);
  return result.exit_code === 0 && !!(result.partial || (result.counts?.attachmentPending ?? 0) > 0);
}

export function attachmentWarning(value: ResultInput): string {
  const pending = resultOf(value).counts?.attachmentPending;
  const count = typeof pending === "number" && Number.isFinite(pending) && pending > 0 ? `${Math.floor(pending)}건은` : "일부는";
  return `과목·과제 정보는 저장했습니다. 첨부파일 ${count} 다음 동기화에서 다시 시도합니다.`;
}

/** Do not expose request URLs, signed download links, or raw exception text. */
export function syncFailureText(value: ResultInput): string {
  const result = resultOf(value);
  if (result.exit_code === 2) return "e클래스 로그인이 만료되었거나 필요합니다. 로그인 창에서 다시 연결하세요.";
  if (result.exit_code === -1) return "수집 시간이 초과되었습니다. 잠시 뒤 다시 시도하세요.";
  if (result.exit_code === 4) {
    if (result.errorKind === "timeout") return "e클래스 요청 시간이 초과되었습니다. 잠시 뒤 다시 시도하세요.";
    if (result.errorKind === "network") return "e클래스 서버 연결 요청에 실패했습니다. 잠시 뒤 다시 시도하세요.";
    return "e클래스 요청에 실패했습니다. 잠시 뒤 다시 시도하세요.";
  }
  return "e클래스 동기화를 완료하지 못했습니다. 수집 원천에서 상태를 확인하세요.";
}

export function syncResultMessage(value: ResultInput): { text: string; ok: boolean; partial: boolean } {
  const result = resultOf(value);
  if (result.exit_code === 0) {
    const ledger = result.ledger;
    const changes = ledger ? [ledger.new && `새 과제 ${ledger.new}건`, ledger.changed && `마감 변경 ${ledger.changed}건`, ledger.submitted && `제출 확인 ${ledger.submitted}건`].filter(Boolean) : [];
    const partial = hasSyncWarning(result);
    const text = partial ? attachmentWarning(result) : "e클래스 동기화 완료";
    return { text: `${text}${changes.length ? ` · ${changes.join(" · ")}` : ""}`, ok: true, partial };
  }
  if (result.exit_code === 3) return { text: "다른 동기화가 이미 진행 중이라 건너뛰었습니다", ok: true, partial: false };
  return { text: syncFailureText(result), ok: false, partial: false };
}

export function syncRunResult(value: ResultInput): { label: string; tone: "ok" | "warn" | "neutral" | "danger" | "info" } {
  const result = resultOf(value);
  switch (result.exit_code) {
    case 0: return hasSyncWarning(result) ? { label: "저장 완료 · 첨부 확인", tone: "warn" } : { label: "성공", tone: "ok" };
    case 2: return { label: "로그인 필요", tone: "warn" };
    case 3: return { label: "건너뜀", tone: "neutral" };
    case 4: return { label: result.errorKind === "timeout" ? "요청 시간 초과" : result.errorKind === "network" ? "서버 연결 실패" : "요청 실패", tone: "danger" };
    case -1: return { label: "수집 시간 초과", tone: "danger" };
    case null: return { label: "진행 중", tone: "info" };
    default: return { label: "실패", tone: "danger" };
  }
}

/** Keep diagnostic messages readable without displaying private request URLs. */
export function redactSyncDetails(text: string): string {
  return text
    .replace(/(?:https?:)?\/\/[^\s<>"']+/gi, "[요청 주소 생략]")
    .replace(/(^|[\s("'=])\/(?!\/)[^\s<>"']+/g, "$1[요청 경로 생략]");
}
