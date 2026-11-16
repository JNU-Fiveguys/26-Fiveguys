// F6 과제·마감 — 백엔드 F6_Eclass_agent/eclass/api.py 와 주고받는 모양 + 화면 문구.
// 과제 한 건의 모양은 /api/events 의 deadline extendedProps(types.ts DeadlineProps)와 같다.

import type { DeadlineProps, SyncState } from "./types";
export { syncRunResult as runResult } from "./eclassSync";

/** GET /api/assignments 의 한 줄 (= 캘린더 deadline 의 extendedProps + id·title·start) */
export interface AssignmentView extends Omit<DeadlineProps, "kind"> {
  id: string;
  title: string;
  start: string | null;
  history?: { kind: string; at: string; before: { due?: string } | null; after: { due?: string } | null }[];
}

export interface AssignmentList {
  items: AssignmentView[];
  counts: { open: number; done: number; past: number };
  updatedAt: string | null;
}

/** 실행 이력 한 줄 (state/runs.jsonl) */
export interface RunRecord {
  started_at: string;
  finished_at?: string | null;
  duration_s?: number | null;
  exit_code: number | null;
  source?: RunSource | null;
  attempt?: number;
  counts?: Record<string, number> | null;
  error?: string | null;
  errorKind?: "timeout" | "network" | null;
  partial?: boolean;
  warnings?: string[];
  ledger?: { new: number; changed: number; removed: number; submitted: number } | null;
  dryRun?: boolean;
}

export type RunSource = "schedule" | "catchup" | "retry" | "button" | "manual";

export interface RetryInfo {
  attempt: number;
  next_at: string;
  source: string;
  slot: string | null;
}

/** 로그인 창(C3) 실행 상태 */
export interface LoginJob {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  ok: boolean | null;
  error: string | null;
  already_running?: boolean;
}

export interface LoginStatus {
  available: boolean;
  installed: boolean;
  browsers: boolean;
  hasSession: boolean;
  sessionSavedAt?: string | null;
  hasCreds: boolean;
  problem: string | null;
}

/** GET·PUT·DELETE /api/login/creds (C3) — 자동 로그인 정보. 비밀번호는 보내기만 하고 돌려받지 않는다 */
export interface LoginCreds {
  saved: boolean;
  /** 저장된 아이디 — 복호화하지 못하면(다른 Windows 계정에서 만든 파일 등) null */
  username: string | null;
  /** dpapi = Windows 이 계정 전용 암호화 · keychain = 맥 로그인 키체인 */
  store: "dpapi" | "keychain";
}

/** /api/status 의 eclass 칸 */
export interface EclassStatus {
  available: boolean;
  error?: string;
  updatedAt?: string | null;
  reconciledAt?: string | null;
  counts?: { total: number; open: number; soon: number; overdue: number; new: number };
  lastOkAt?: string | null;
  failureStreak?: number;
  failureSince?: string | null;
  lastError?: string | null;
  warn?: boolean; // 연속 3회 실패 (F6-R15)
  needLogin?: boolean; // 마지막 실행이 로그인 필요(코드 2)
  retry?: RetryInfo | null;
  intervalHours?: number;
  nextSlot?: string;
  login?: LoginJob;
  feed?: FeedSummary | null; // 새 글·자료 — 안 읽은 수 · 안 읽은 공지 · updatedAt
}

export interface TaskInfo {
  available: boolean;
  registered: boolean;
  name?: string;
  state?: string;
  nextRun?: string | null;
  lastRun?: string | null;
  lastResult?: number | null;
  pathOk?: boolean;
  legacy?: { name: string; state: string; pathOk: boolean }[];
  error?: string;
}

/** GET /api/sources/eclass (F6-S08) */
export interface EclassSource {
  key: "eclass";
  intervalHours: number;
  choices: number[];
  slots: string[];
  nextSlot: string;
  task: TaskInfo;
  runs: RunRecord[];
  streak: { count: number; since: string | null; lastOkAt: string | null; lastError: string | null; lastCode: number | null };
  warnAt: number;
  retry: RetryInfo | null;
  sync: SyncState;
  login: LoginStatus;
  loginJob: LoginJob;
  log: string[];
  dataDir: string;
}

export interface ReminderSettings {
  reminders: string[];
  alertTime: string;
  earlyDueHours: number;
  setAt: string | null;
  choices: { code: string; label: string }[];
  planned?: { id: string; code: string; at: string; title: string }[];
}

export const RUN_SOURCE_LABEL: Record<RunSource, string> = {
  schedule: "예약",
  catchup: "따라잡기",
  retry: "재시도",
  button: "지금 수집",
  manual: "명령줄",
};

/** 소요 초 → '38초' · '1분 19초' */
export function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined) return "";
  const n = Math.round(s);
  return n < 60 ? `${n}초` : `${Math.floor(n / 60)}분${n % 60 ? ` ${n % 60}초` : ""}`;
}

/* ---------------------------------------------------------------- E클래스 새 글·자료 (F6 feed) */

export type FeedKind = "notice" | "board" | "material";

export interface FeedSummary {
  total: number;
  unread: number;
  unreadNotices: number;
  updatedAt: string | null;
}

/** GET /api/eclass/feed 의 한 줄 — 공지 게시판 글 · 자료실 글 · 강의자료 파일 */
export interface FeedItem {
  id: string; // post:<게시판 cmid>:<글 번호> · file:<해시>
  kind: FeedKind;
  kindLabel: string;
  course: string;
  courseId: string;
  courseShort: string;
  board: string; // 게시판 이름 · 자료 활동 이름
  title: string;
  url: string; // e클래스 글·활동 주소
  postedAt: string | null; // 글 작성일 (파일은 없음)
  fetchedAt: string | null; // 받은 시각
  firstSeen: string;
  size: number | null;
  attachments: string[]; // 첨부 파일 이름
  read: boolean;
  isNew: boolean; // 기준선 이후 새로 봤고 아직 안 읽음
  removed: boolean;
  excerpt: string;
  localPath: string;
  body?: string; // 상세에서만
}

export interface FeedList {
  items: FeedItem[];
  counts: { all: number; unread: number; notice: number; board: number; material: number };
  updatedAt: string | null;
  settings: FeedSettings;
}

export interface FeedSettings {
  notices: boolean; // 새 공지 — 한 건씩 알림
  materials: boolean; // 새 자료실 글·강의자료 — 하루치 묶음 알림
}

export const FEED_KIND_LABEL: Record<FeedKind, string> = { notice: "공지", board: "자료실 글", material: "강의자료" };
