// 백엔드(/api) 와 주고받는 형태. 백엔드 app/main.py 의 Pydantic 모델과 짝이다.
// 학사 일정(F1)은 F1_Bachelor_agent/bachelor/api.py — 자세한 모양은 lib/academic.ts.

import type { AcademicType, ReminderOpt } from "./academic";
import type { EclassStatus, RetryInfo, RunSource } from "./assignments";
import type { AttendanceStatus, ClassProps } from "./attendance";
import type { ExamsStatus } from "./exams";
import type { PriorityStatus } from "./priority";
import type { PlacementStatus, StudyProps } from "./placement";
import type { GraduationSummary } from "./graduation";
import type { MaterialsStatus } from "./materials";
import type { ScholarshipStatus } from "./scholarships";
import type { ActivityStatus } from "./activities";
import type { BriefingStatus } from "./briefing";

export type { ClassProps } from "./attendance";
export type { StudyProps } from "./placement";

export type CategoryKey = "personal" | "study" | "team" | "etc";

export interface CategoryInfo {
  label: string;
  color: string;
}

export interface Course {
  id: string;
  name: string;
  short: string;
  code: string;
  section: string;
  url: string;
  color: string;
  activityCount: number;
}

/** /api/events 의 과제·퀴즈·동영상 마감 (F6) — id 는 'dl:<e클래스 활동 번호>' */
export interface DeadlineProps {
  kind: "deadline";
  cmid: string;
  type: string; // 과제 · 퀴즈 · 동영상 · 일정
  source: string; // assign · calendar · quiz
  due: string; // ISO (로컬)
  course: string;
  courseId: string;
  courseShort: string;
  courseCode: string;
  courseColor: string;
  status: string; // e클래스 '제출 여부' 원문
  submitted: boolean; // e클래스 제출 완료
  graded: string;
  description: string;
  attachments: { name: string; path: string }[];
  attachmentCount: number;
  url: string;
  userDone: boolean; // 내가 체크함 (F6-R30) — 제출 완료면 false (승격)
  userDoneAt: string | null;
  promoted: boolean; // 내가 체크함 → e클래스 제출 완료로 승격됨 (F6-R32)
  changed: { at: string; before: string | null } | null; // 마감이 바뀐 이력 (F6-R22)
  estimateHours: number | null; // 사용자가 고친 예상 소요시간 (F7) — null 이면 기본값
  firstSeen: string;
  isNew: boolean; // 처음 본 지 하루 안
  removed: boolean;
  done: boolean;
  overdue: boolean;
  legacyId: string | null; // 예전 대시보드 id (브라우저 값 옮기기용)
}

export interface UserProps {
  kind: "user";
  category: CategoryKey;
  categoryLabel: string;
  color: string;
  memo: string;
  isTodo: boolean; // 할 일 — To Do List 에서 완료 체크
  done: boolean;
  /** 가져온 곳 — 학사 일정에서 '내 일정에 넣기'로 만든 것이면 그 학사 일정 id('ac:…') */
  origin: string | null;
}

/** /api/events 에 섞여 오는 학사 일정 (F1) — 내 캘린더에 등록된 것(onCalendar)만 온다 */
export interface AcademicProps {
  kind: "academic";
  refId: string; // 학사 일정 id — 두 주가 넘는 기간은 '시작'·'마감' 두 점(id 에 #start·#end)으로 오므로 상세는 이 id 로 연다
  marker: "start" | "end" | null;
  type: AcademicType;
  typeLabel: string;
  color: string;
  appliesToMe: boolean | null;
  status: "auto" | "approved";
  changed: boolean;
  endTime: string | null;
  source: string;
  reminders: ReminderOpt[];
}

/** /api/events 의 시험 (F5, C1 3절 kind=exam) — id 는 시험 id 그대로('ex:…'). 캘린더에서는 보기만 한다 */
export interface ExamProps {
  kind: "exam";
  examId: string;
  courseId: string;
  courseName: string;
  course: string;
  courseColor: string;
  color: string;
  type: string; // midterm · final · quiz · presentation · etc
  typeLabel: string;
  place: string;
  timeUnknown: boolean; // 공지에 시각이 없었다 → 종일 일정으로 그린다 (F5 8절)
  status: "confirmed" | "review";
  needsReview: boolean;
  confidence: number;
  source: "notice" | "manual" | "auto";
  noticeUrl: string;
  evidence: { quote: string; url: string }[];
  /** 임의 일정 — 수업평가 기간·수업 요일로 잡아 둔 자리 (공지가 나오면 바뀐다) */
  isAuto: boolean;
  note: string;
  dday: number;
  href: string;
}

export interface CalEvent {
  id: string;
  title: string;
  start: string; // ISO (로컬) 또는 YYYY-MM-DD (종일)
  end: string | null; // 종일이면 exclusive 날짜
  allDay: boolean;
  editable: boolean;
  extendedProps: DeadlineProps | UserProps | AcademicProps | ClassProps | ExamProps | StudyProps;
}

export interface UserEventInput {
  title: string;
  start: string;
  end: string | null;
  all_day: boolean;
  category: CategoryKey;
  memo: string;
  is_todo: boolean;
  done: boolean;
  /** 만들 때만 — 학사 일정 id. 같은 학사 일정을 두 번 넣으면 409 */
  origin?: string | null;
}

export interface SyncState {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null; // 0 핵심 정보 저장 완료 · 1 오류 · 2 로그인 필요 · 3 건너뜀 · 4 요청 실패 · -1 수집 시간 초과
  source: "button" | "external" | null; // button = 대시보드에서 누름, external = 예약 작업(작업 스케줄러)·명령줄
  runSource?: RunSource | null; // 실행 이력의 원래 값 — schedule · catchup · retry · button · manual
  attempt?: number; // 재시도 회차 (1 = 첫 시도)
  pid: number | null;
  retry?: RetryInfo | null; // 요청 실패 뒤 다음 재시도를 기다리는 중
  counts?: Record<string, number> | null;
  ledger?: { new: number; changed: number; removed: number; submitted: number } | null;
  already_running?: boolean; // POST /api/sync: 이미 돌고 있어서 새로 띄우지 않음
  error?: string | null;
  errorKind?: "timeout" | "network" | null;
  partial?: boolean; // 핵심 정보는 저장했지만 첨부 재시도가 남아 있음
  warnings?: string[];
}

export interface Status {
  updated_at: string | null; // 마지막으로 e클래스 수집에 성공한 시각
  eclass?: EclassStatus; // F6 — 진행 중·지난 마감 · 연속 실패 · 로그인 필요 · 재시도 · updatedAt
  sync: SyncState;
  counts: { courses: number; deadlines: number; userEvents: number; todos: number; todosDone: number };
  categories: Record<CategoryKey, CategoryInfo>;
  eclassDataDir: string;
  log: string[];
  academic?: AcademicStatus;
  graduation?: GraduationSummary; // F2 — 기능 타일 숫자 · updatedAt
  attendance?: AttendanceStatus; // F3 — 위험 과목 · 확인 안 한 수업 · updatedAt
  materials?: MaterialsStatus; // F4 — 강의자료 수 · 쪽수 · 확인 필요 · updatedAt
  exams?: ExamsStatus; // F5 — 오늘 분량 · 다가오는 시험 · 확인 필요 · 밀린 계획 · updatedAt
  priority?: PriorityStatus; // F7 — 지금 해야 함 · 합계 · 놓친 마감 · 상위 3건
  placement?: PlacementStatus; // F8 — 앞으로 남은 학습 블록 · 오늘 블록 · updatedAt
  scholarship?: ScholarshipStatus; // F11 — 해당 · 확인 필요 · 마감 임박 · 수집 상태 · updatedAt
  activity?: ActivityStatus; // F12 — 내 관심사 · 관련 있음 · 담은 것 · 마감 임박 · 수집 상태 · updatedAt
  briefing?: BriefingStatus; // C4 — 오늘 브리핑 상태(ready·before·pending·off) · 한 줄 · 건수 · updatedAt
}

/* ---------------------------------------------------------------- F1 학사 원천·수집 */

/** needs_profile = 프로필에 소속이 없어 대기 · not_found = 소속 홈페이지를 학교 목록에서 못 찾음 (③·④) */
export type SourceState = "ok" | "failed" | "format_changed" | "never" | "needs_profile" | "not_found";

/** F1 수집 실행 상태. exit_code: 0 성공 · 1 일부 원천 실패 · 3 다른 실행이 진행 중 · 4 전부 실패 */
export interface AcademicSync {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  source: "button" | "external" | null;
  keys: string[] | null;
  results: Record<string, { result: string; count?: number; error?: string }> | null;
  /** 누가 띄웠나 — button · manual · schedule(매일 08시) · catchup(놓친 것 따라잡기) · retry */
  by?: string | null;
  /** 예약 수집이 실패해 재시도를 기다리는 중 */
  retry?: { attempt: number; next_at: string } | null;
  already_running?: boolean;
  error?: string;
}

/** 학사일정 예약 수집(작업 스케줄러) 상태 */
export interface AcademicSchedule {
  available: boolean;
  registered: boolean;
  at: string; // '08:00'
  nextRun?: string | null;
  lastRun?: string | null;
  lastResult?: number | null;
  pathOk?: boolean;
  /** false 면 예전 방식(06·18시) 등록 — 다시 등록해야 한다 */
  scheduled?: boolean;
  error?: string;
}

export interface AcademicSourceState {
  key: string;
  name: string;
  kindLabel: string;
  state: SourceState;
  lastRunAt: string | null;
  lastOkAt: string | null;
  count: number | null;
  error: string | null;
  enabled: boolean;
  /** 지금 목록·캘린더에 이 원천에서 온 항목 수 (끄면 빠지고, 켜면 돌아온다) */
  items: number;
  /** ③·④ 만 — college | dept */
  scope: "college" | "dept" | null;
  /** 프로필의 소속 이름 (예: 인공지능학부) */
  target: string | null;
  /** ok = 홈페이지를 찾음 · needs_profile = 프로필에 소속 없음 · not_found = 학교 목록에 없음 */
  resolve: "ok" | "needs_profile" | "not_found" | null;
  /** 마지막으로 받은 뒤 소속·지정 주소가 바뀜 → 다음 수집에서 새 게시판을 읽는다 */
  stale: boolean;
}

/** /api/status 의 academic 칸 */
export interface AcademicStatus {
  available: boolean;
  error?: string;
  updatedAt?: string | null;
  sync?: AcademicSync;
  reviewCount?: number;
  /** 마지막 수집에서 새로 찾아 바로 등록된 일정 수 (확인 필요는 reviewCount) */
  newCount?: number;
  /** 마지막 수집 시작 시각 — 이 뒤에 처음 본 일정이 '신규' */
  lastRunAt?: string | null;
  counts?: { total: number; mine: number; thisMonth: number };
  sources?: AcademicSourceState[];
}

/** /api/sources 의 한 행 (F1-S12) — 원천 4곳: jnu_calendar · jnu_notice · my_dept · my_college */
export interface SourceRowApi extends AcademicSourceState {
  kind: string;
  /** 원문 보기 주소 — ③·④ 는 읽는 게시판(없으면 홈페이지) */
  url: string | null;
  interval: string;
  builtin: boolean;
  /** 학교 목록에서의 이름 (프로필 이름과 표기가 다를 때 보여 준다) */
  listedAs: string | null;
  homepage: string | null;
  /** 읽는 게시판 — auto = 홈페이지 메뉴에서 찾음 · manual = 사용자가 지정 */
  board: { label: string | null; url: string; category: string | null; via: "auto" | "manual"; checkedAt: string | null } | null;
  overrideUrl: string | null;
}

export interface SourcesResponse {
  sources: SourceRowApi[];
  sync: AcademicSync;
  log: string[];
  /** 단과대학·학부 홈페이지 목록 (전남대 대표 홈페이지 '대학·학부(과)' 안내에서) */
  directory: { updatedAt: string | null; colleges: number; departments: number; source: "local" | "bundled" };
  schedule: AcademicSchedule;
}

/* ---------------------------------------------------------------- 알림 (F1 이 처음 만들고 F3·F5·F6·F11 이 같이 쓴다) */

export interface AppNotification {
  id: string;
  kind: "academic" | "change" | "system" | "deadline" | "briefing" | "opportunity" | "scholarship" | "attendance" | "exam" | "eclass";
  refId: string | null;
  title: string;
  body: string;
  at: string; // 울릴 예정이던 시각
  deliveredAt: string;
  read: boolean;
  missed: boolean; // PC 가 꺼져 있어 늦게 배달됨
  href: string | null; // 상세 모달이 열린 URL
}

export interface NotificationList {
  items: AppNotification[];
  unread: number;
  bundleOver: number; // 같은 시각 알림이 이보다 많으면 한 줄로 묶는다
}
