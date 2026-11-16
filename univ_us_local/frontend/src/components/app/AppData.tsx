"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { AcademicEvent, AcademicOverview, AcademicPatch } from "@/lib/academic";
import type { ProfileDoc, ProfilePatch } from "@/lib/profile";
import type { CalEvent, Course, DeadlineProps, Status } from "@/lib/types";
import { syncResultMessage } from "@/lib/eclassSync";
import { useToast } from "@/components/ui/Toast";

// 모든 화면이 같이 보는 실제 데이터(e클래스 마감·내 일정·학사 일정·프로필·과목·동기화 상태).
// 동기화 버튼은 여러 화면에 있어도 실행은 하나 — /api/status 하나를 보고 함께 비활성된다(Frontend-Route 11-5).
// 폴링: 동기화(e클래스·학사) 중 3초, 평소 60초. updated_at · academic.updatedAt 이 바뀌었을 때만 다시 부른다(6-9).

interface AppData {
  events: CalEvent[];
  courses: Course[];
  status: Status | null;
  loading: boolean;
  error: string | null;
  syncing: boolean;
  courseColor: (idOrName: string) => string | undefined;
  refresh: () => Promise<void>;
  startSync: () => Promise<void>;
  setEvents: React.Dispatch<React.SetStateAction<CalEvent[]>>;
  // F6 과제 — 내가 체크함·소요시간 (서버 저장, 화면은 먼저 바꾼다) · 로그인 창
  patchAssignment: (id: string, body: { userDone?: boolean; estimatedHours?: number | null }) => Promise<boolean>;
  loginRunning: boolean;
  startLogin: () => Promise<void>;
  // F1 학사일정
  academic: AcademicOverview | null;
  academicError: string | null;
  academicSyncing: boolean;
  refreshAcademic: () => Promise<void>;
  updateAcademic: (id: string, patch: AcademicPatch) => Promise<AcademicEvent | null>;
  startAcademicSync: (key?: string) => Promise<void>;
  // C2 프로필 (F1 대상 판정 · F2 · F11 이 같이 쓴다)
  profile: ProfileDoc | null;
  profileError: string | null;
  refreshProfile: () => Promise<ProfileDoc | null>;
  updateProfile: (patch: ProfilePatch) => Promise<ProfileDoc | null>;
}

const Ctx = createContext<AppData | null>(null);

export function useAppData(): AppData {
  const v = useContext(Ctx);
  if (!v) throw new Error("AppDataProvider 가 없습니다");
  return v;
}

// F1 수집 종료 코드 → 문구 (F1_Bachelor_agent/bachelor/pipeline.py 머리 주석)
export function academicResultMessage(code: number | null): { text: string; ok: boolean } {
  switch (code) {
    case 0:
      return { text: "학사일정 수집 완료", ok: true };
    case 1:
      return { text: "일부 원천을 수집하지 못했습니다 — 수집 원천에서 확인하세요", ok: false };
    case 3:
      return { text: "다른 학사일정 수집이 진행 중이라 건너뛰었습니다", ok: true };
    case 4:
      return { text: "학사일정 수집 실패 — 인터넷 연결을 확인하세요", ok: false };
    default:
      return { text: `학사일정 수집이 끝나지 않았습니다 (코드 ${code}) — F1_Bachelor_agent/state/sync.log 확인`, ok: false };
  }
}

/** 확인 필요 → 승인 등 사용자 조작을 서버 응답 전에 먼저 반영한다(낙관적 업데이트, C1-R32) */
function applyPatch(e: AcademicEvent, p: AcademicPatch): AcademicEvent {
  const next = { ...e };
  if (p.status === "approved") next.status = "approved";
  if (p.status === "hidden") next.status = "hidden";
  if (p.status === "restore") next.status = e.confidence >= 0.8 && !e.needsOcr ? "auto" : "review";
  if (p.memo !== undefined) next.memo = p.memo;
  if (p.pinned !== undefined) next.pinned = p.pinned;
  if (p.reminders) next.reminders = e.reminders.map((r) => (r.code in p.reminders! ? { ...r, enabled: !!p.reminders![r.code] } : r));
  next.onCalendar =
    next.status !== "hidden" && !!next.start && (next.status === "approved" || next.pinned || (next.status === "auto" && next.appliesToMe === true));
  return next;
}

export function AppDataProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [academic, setAcademic] = useState<AcademicOverview | null>(null);
  const [academicError, setAcademicError] = useState<string | null>(null);
  const [profile, setProfile] = useState<ProfileDoc | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const lastUpdated = useRef<string | null | undefined>(undefined);
  const lastAcademic = useRef<string | null | undefined>(undefined);
  const lastAttendance = useRef<string | null | undefined>(undefined); // F3 — 수업 회차(캘린더 class)가 바뀌었는지
  const lastEclass = useRef<string | null | undefined>(undefined); // F6 — 과제 원장(신규·변경·내가 체크함)이 바뀌었는지
  const lastPlacement = useRef<string | null | undefined>(undefined); // F8 — 학습 블록(배치·옮김·완료)이 바뀌었는지

  const refreshProfile = useCallback(async () => {
    try {
      const p = await api.profile();
      setProfile(p);
      setProfileError(null);
      return p;
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, []);

  const refreshAcademic = useCallback(async () => {
    try {
      const a = await api.academic();
      setAcademic(a);
      lastAcademic.current = a.updatedAt;
      setAcademicError(null);
    } catch (e) {
      setAcademicError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [ev, st, cs] = await Promise.all([api.events(), api.status(), api.courses()]);
      setEvents(ev);
      setStatus(st);
      setCourses(cs);
      lastUpdated.current = st.updated_at;
      lastAttendance.current = st.attendance?.updatedAt;
      lastEclass.current = st.eclass?.updatedAt;
      lastPlacement.current = st.placement?.updatedAt;
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(async () => {
      await migrateLocalProfile();          // 예전엔 프로필이 브라우저에만 있었다 — 서버가 비어 있으면 한 번 옮긴다
      await Promise.all([refresh(), refreshAcademic(), refreshProfile()]);
    });
  }, [refresh, refreshAcademic, refreshProfile]);

  const running = !!status?.sync.running;
  const academicRunning = !!status?.academic?.sync?.running;
  const loginRunning = !!status?.eclass?.login?.running;
  const prev = useRef({ running, academicRunning, loginRunning });
  useEffect(() => {
    const id = window.setInterval(
      async () => {
        try {
          const st = await api.status();
          const was = prev.current;
          const acRunning = !!st.academic?.sync?.running;
          const lgRunning = !!st.eclass?.login?.running;
          prev.current = { running: st.sync.running, academicRunning: acRunning, loginRunning: lgRunning };
          setStatus(st);
          setError(null);
          if (was.loginRunning && !lgRunning) {
            const lg = st.eclass?.login;
            toast(lg?.ok ? "로그인되었습니다 — 바로 수집합니다" : `로그인하지 못했습니다${lg?.error ? `: ${lg.error}` : ""}`, { tone: lg?.ok ? "success" : "error" });
          }
          if (was.running && !st.sync.running) {
            await refresh();
            const r = syncResultMessage(st.sync);
            toast(r.text, { tone: r.partial ? "default" : r.ok ? "success" : "error" });
          } else if (
            st.updated_at !== lastUpdated.current ||
            (st.attendance?.updatedAt ?? null) !== (lastAttendance.current ?? null) ||
            (st.eclass?.updatedAt ?? null) !== (lastEclass.current ?? null) ||
            (st.placement?.updatedAt ?? null) !== (lastPlacement.current ?? null)
          ) {
            await refresh(); // e클래스 수집 결과·과제 원장(다른 창에서 체크) · 출결(수업 회차·휴강·출결 칩) · 학습 블록이 바뀌었다
          }
          if (was.academicRunning && !acRunning) {
            await Promise.all([refresh(), refreshAcademic()]);
            const r = academicResultMessage(st.academic?.sync?.exit_code ?? null);
            const n = st.academic?.newCount ?? 0;
            const rv = st.academic?.reviewCount ?? 0;
            const extra = r.ok && (n > 0 || rv > 0) ? ` — 신규 ${n}건${rv > 0 ? ` · 확인 필요 ${rv}건` : ""}` : "";
            toast(r.text + extra, { tone: r.ok ? "success" : "error" });
          } else if (st.academic?.updatedAt !== undefined && st.academic.updatedAt !== lastAcademic.current) {
            await Promise.all([refresh(), refreshAcademic()]);
          }
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      },
      running || academicRunning || loginRunning ? 3000 : 60_000,
    );
    return () => window.clearInterval(id);
  }, [running, academicRunning, loginRunning, refresh, refreshAcademic, toast]);

  // 내가 체크함·소요시간 — 화면을 먼저 바꾸고(낙관적) 실패하면 되돌린다 (Frontend-Route 11-4)
  const patchAssignment = useCallback(
    async (id: string, body: { userDone?: boolean; estimatedHours?: number | null }) => {
      let before: CalEvent | undefined;
      setEvents((xs) =>
        xs.map((x) => {
          if (x.id !== id || x.extendedProps.kind !== "deadline") return x;
          before = x;
          const p: DeadlineProps = { ...x.extendedProps };
          if (body.userDone !== undefined) {
            p.userDone = body.userDone && !p.submitted;
            p.done = p.submitted || p.userDone;
          }
          if (body.estimatedHours !== undefined) p.estimateHours = body.estimatedHours;
          return { ...x, extendedProps: p };
        }),
      );
      try {
        const res = await api.patchAssignment(id, body);
        if (res.event) setEvents((xs) => xs.map((x) => (x.id === id ? res.event! : x)));
        lastEclass.current = undefined; // 다음 상태 확인 때 원장 도장을 새로 받는다
        return true;
      } catch (e) {
        if (before) setEvents((xs) => xs.map((x) => (x.id === id ? before! : x)));
        toast(`저장하지 못했습니다: ${e instanceof Error ? e.message : String(e)}`, { tone: "error" });
        return false;
      }
    },
    [toast],
  );

  // 로그인 창 (C3) — 이 PC 화면에 브라우저 창이 뜬다. 로그인되면 서버가 바로 수집을 시작한다 (F6-S10)
  const startLogin = useCallback(async () => {
    try {
      const st = await api.startLogin();
      prev.current = { ...prev.current, loginRunning: st.running };
      setStatus((p) => (p ? { ...p, eclass: { ...(p.eclass ?? { available: true }), login: st } } : p));
      toast(st.already_running ? "로그인 창이 이미 열려 있습니다" : "로그인 창을 열었습니다 — 학교 로그인(+휴대폰 인증)을 마치면 저절로 닫힙니다");
    } catch (e) {
      toast(`로그인 창을 열지 못했습니다: ${e instanceof Error ? e.message : String(e)}`, { tone: "error" });
    }
  }, [toast]);

  const startSync = useCallback(async () => {
    try {
      const st = await api.startSync();
      prev.current = { ...prev.current, running: st.running };
      setStatus((p) => (p ? { ...p, sync: st } : p));
      if (st.error) toast(st.error, { tone: "error" });
      else if (st.already_running)
        toast(st.source === "external" ? "예약 동기화가 이미 진행 중입니다 — 끝나면 알려드립니다" : "이미 동기화가 진행 중입니다");
      else toast("e클래스 동기화를 시작했습니다");
    } catch (e) {
      toast(`동기화를 시작하지 못했습니다: ${e instanceof Error ? e.message : String(e)}`, { tone: "error" });
    }
  }, [toast]);

  const startAcademicSync = useCallback(
    async (key = "academic") => {
      try {
        const st = await api.syncSource(key);
        prev.current = { ...prev.current, academicRunning: st.running };
        setStatus((p) => (p ? { ...p, academic: { ...(p.academic ?? { available: true }), sync: st } } : p));
        if (st.error) toast(st.error, { tone: "error" });
        else if (st.already_running) toast("학사일정 수집이 이미 진행 중입니다 — 끝나면 알려드립니다");
        else toast("학사일정 수집을 시작했습니다 — 1분쯤 걸립니다");
      } catch (e) {
        toast(`수집을 시작하지 못했습니다: ${e instanceof Error ? e.message : String(e)}`, { tone: "error" });
      }
    },
    [toast],
  );

  const updateAcademic = useCallback(
    async (id: string, patch: AcademicPatch) => {
      let before: AcademicOverview | null = null;
      setAcademic((a) => {
        before = a;
        return a ? { ...a, items: a.items.map((e) => (e.id === id ? applyPatch(e, patch) : e)) } : a;
      });
      try {
        const saved = await api.updateAcademic(id, patch);
        setAcademic((a) => (a ? { ...a, items: a.items.map((e) => (e.id === id ? saved : e)) } : a));
        // 캘린더(/api/events)·확인 필요 건수(/api/status)도 바뀐다
        void Promise.all([api.events(), api.status()]).then(([ev, st]) => {
          setEvents(ev);
          setStatus(st);
          lastAcademic.current = st.academic?.updatedAt;
        });
        return saved;
      } catch (e) {
        setAcademic(before);
        toast(`저장하지 못했습니다: ${e instanceof Error ? e.message : String(e)}`, { tone: "error" });
        return null;
      }
    },
    [toast],
  );

  // 프로필 저장 → '내 해당'(F1)·캘린더가 다시 계산되므로 같이 다시 부른다 (Frontend-Route 5-4)
  const updateProfile = useCallback(
    async (patch: ProfilePatch) => {
      try {
        const doc = await api.patchProfile(patch);
        setProfile(doc);
        if (doc.changed?.length) void Promise.all([refresh(), refreshAcademic()]);
        return doc;
      } catch (e) {
        toast(`저장하지 못했습니다: ${e instanceof Error ? e.message : String(e)}`, { tone: "error" });
        return null;
      }
    },
    [refresh, refreshAcademic, toast],
  );

  const colorMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of courses) {
      m.set(c.id, c.color);
      m.set(c.short, c.color);
      m.set(c.name, c.color);
    }
    return m;
  }, [courses]);

  const courseColor = useCallback(
    (key: string) => colorMap.get(key) ?? [...colorMap.entries()].find(([k]) => k.startsWith(key.replace(/\[.*$/, "")))?.[1],
    [colorMap],
  );

  const value = useMemo<AppData>(
    () => ({
      events,
      courses,
      status,
      loading,
      error,
      syncing: running,
      courseColor,
      refresh,
      startSync,
      setEvents,
      patchAssignment,
      loginRunning,
      startLogin,
      academic,
      academicError,
      academicSyncing: academicRunning,
      refreshAcademic,
      updateAcademic,
      startAcademicSync,
      profile,
      profileError,
      refreshProfile,
      updateProfile,
    }),
    [events, courses, status, loading, error, running, courseColor, refresh, startSync, patchAssignment, loginRunning, startLogin, academic, academicError, academicRunning, refreshAcademic, updateAcademic, startAcademicSync, profile, profileError, refreshProfile, updateProfile],
  );

  return (
    <Ctx.Provider value={value}>{children}</Ctx.Provider>
  );
}

/* ---------------------------------------------------------------- 옛 브라우저 프로필 옮기기 */

const LEGACY_KEY = "univus:profile";

/** 프로필이 브라우저(localStorage)에만 있던 때의 값을 서버(C2)로 한 번 옮기고 지운다. 서버에 이미 있으면 서버가 이긴다. */
async function migrateLocalProfile(): Promise<void> {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(LEGACY_KEY);
  } catch {
    return;
  }
  if (!raw) return;
  try {
    await api.migrateProfile(JSON.parse(raw));
    window.localStorage.removeItem(LEGACY_KEY);
  } catch {
    /* 서버가 꺼져 있으면 다음에 다시 */
  }
}
