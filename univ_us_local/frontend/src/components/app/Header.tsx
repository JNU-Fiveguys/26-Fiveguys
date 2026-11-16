"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Bell,
  CalendarClock,
  ChevronRight,
  Gift,
  HandCoins,
  Landmark,
  Megaphone,
  Plus,
  RefreshCw,
  Settings,
  Sun,
  Target,
  TriangleAlert,
  UserCheck,
  WifiOff,
} from "lucide-react";
import { Popover } from "@/components/ui/Layout";
import { useAppData } from "./AppData";
import { BrandMark } from "./BrandMark";
import { EclassFailureStrip } from "@/components/assignments/EclassSyncBanner";
import { navigateQuery } from "@/lib/useQueryState";
import { api } from "@/lib/api";
import type { AppNotification, NotificationList } from "@/lib/types";
import { SETTINGS } from "@/lib/features";
import { fmtRelative, parseLocal } from "@/lib/dates";
import { hasSyncWarning } from "@/lib/eclassSync";

// 헤더 — 로고 · 알림 · 일정 추가 · 설정. 메뉴 링크(GNB)는 두지 않는다(Frontend-Route 3절, 전역 결정 G4).

export function useOpenNewEvent() {
  const pathname = usePathname();
  const router = useRouter();
  return (kind: "event" | "todo" = "event") => {
    if (pathname === "/") navigateQuery({ new: kind }, "push");
    else router.push(`/?new=${kind}`);
  };
}

export default function Header() {
  const { error, status, syncing } = useAppData();
  const openNew = useOpenNewEvent();

  return (
    <>
      {/* 떠 있는 섬 헤더 — 화면 위에 붙지 않고 종이 위에 한 장 떠 있다. 흐림(blur)은 이 고정 층에만 */}
      <header className="sticky top-0 z-40 px-3 pt-3 pb-2 md:px-5">
        <div
          className="mx-auto flex h-[var(--header-island)] max-w-[1560px] items-center gap-3 rounded-2xl border border-border bg-surface/80 px-3 backdrop-blur-xl md:px-4"
          style={{ boxShadow: "var(--shadow-lift), var(--inner-hi)" }}
        >
          <Link href="/" className="flex items-center gap-2.5 rounded-xl pr-2" aria-label="유니버스 대시보드로">
            <BrandMark size={32} className="flex-none rounded-[7px]" style={{ boxShadow: "var(--shadow-card)" }} />
            <span className="text-[17px] font-extrabold tracking-tight">유니버스</span>
            <span translate="no" className="hidden text-[12px] font-semibold tracking-wide text-sand-text sm:inline" style={{ fontFamily: "var(--font-display)" }}>
              Univ-Us
            </span>
          </Link>

          <div className="ml-auto flex items-center gap-1.5">
            {status && (
              <Link
                href="/settings/sources"
                className="mr-1 hidden h-8 items-center gap-1.5 rounded-md px-2 text-[13px] text-muted transition-colors hover:bg-surface-3 hover:text-text md:inline-flex"
                title="수집 원천 상태"
              >
                {syncing ? (
                  <>
                    <RefreshCw className="size-3.5 animate-spin text-primary" aria-hidden />
                    {status.sync.source === "external" ? "예약 동기화 진행 중…" : "동기화 중…"}
                  </>
                ) : (
                  <>
                    <span
                      className={`size-2 rounded-full ${hasSyncWarning(status.sync) ? "bg-warn" : status.eclass?.warn ? "bg-danger" : status.sync.exit_code === 0 || status.sync.exit_code === null || status.sync.exit_code === 3 ? "bg-ok" : "bg-warn"}`}
                      aria-hidden
                    />
                    수집 {fmtRelative(status.eclass?.lastOkAt ?? status.updated_at)}
                  </>
                )}
              </Link>
            )}
            <NotificationButton />
            <button type="button" className="btn btn-primary btn-sm hidden md:inline-flex" onClick={() => openNew("event")}>
              <Plus aria-hidden />
              일정 추가
            </button>
            <SettingsMenu />
          </div>
        </div>
        {error && (
          <div
            role="alert"
            className="mx-auto mt-2 flex max-w-[1560px] items-center justify-center gap-2 rounded-xl border px-4 py-2 text-center text-[13px] font-semibold text-danger-text"
            style={{ background: "var(--danger-soft)", borderColor: "color-mix(in oklab, var(--danger) 35%, transparent)" }}
          >
            <WifiOff className="size-4 flex-none" aria-hidden />
            로컬 서버에 연결할 수 없습니다 — 트레이에서 유니버스를 종료한 뒤 다시 실행하세요.
          </div>
        )}
        {!error && <EclassFailureStrip />}
      </header>

      {/* 폰: 일정 추가는 오른쪽 아래 떠 있는 버튼(3-2) */}
      <motion.button
        type="button"
        onClick={() => openNew("event")}
        whileTap={{ scale: 0.94 }}
        className="fixed right-4 bottom-5 z-40 grid size-14 place-items-center rounded-2xl bg-primary text-on-primary md:hidden"
        style={{ boxShadow: "var(--shadow-pop)" }}
        aria-label="일정 추가"
      >
        <Plus className="size-6" aria-hidden />
      </motion.button>
    </>
  );
}

const KIND_ICON: Record<AppNotification["kind"], React.ReactNode> = {
  academic: <Landmark aria-hidden />,
  deadline: <CalendarClock aria-hidden />,
  change: <RefreshCw aria-hidden />,
  briefing: <Sun aria-hidden />,
  opportunity: <Gift aria-hidden />,
  scholarship: <HandCoins aria-hidden />,
  attendance: <UserCheck aria-hidden />,
  exam: <Target aria-hidden />,
  eclass: <Megaphone aria-hidden />,
  system: <TriangleAlert aria-hidden />,
};

/** 같은 시각에 알림이 많으면 한 줄로 묶는다 (F1 9절 '같은 시각에 알림 20건') */
type Group = { key: string; items: AppNotification[] };
function groupByTime(list: AppNotification[], over: number): Group[] {
  const out: Group[] = [];
  for (const n of list) {
    const k = `${n.at.slice(0, 16)}|${n.missed}`;
    const g = out.find((x) => x.key === k);
    if (g) g.items.push(n);
    else out.push({ key: k, items: [n] });
  }
  return out.flatMap((g) => (g.items.length > over ? [g] : g.items.map((n) => ({ key: n.id, items: [n] }))));
}

function NotificationButton() {
  const router = useRouter();
  const { status } = useAppData();
  const [data, setData] = useState<NotificationList | null>(null);
  const [failed, setFailed] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      setData(await api.notifications());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  // 처음 + 1분마다 + 학사·출결 데이터가 바뀔 때 (알림 배달은 서버가 목록을 부를 때 계산한다 — 놓친 알림 포함)
  // 출결 경고(F3)는 상태가 올라가는 순간 서버가 알림 표에 넣는다 → 출결 updatedAt 이 바뀌면 다시 받는다
  // 과제 마감 알림(F6)은 서버가 상태 확인(1분) 때 알림 표에 넣는다 → 과제 원장·마지막 실행이 바뀌면 다시 받는다
  const academicStamp = status?.academic?.updatedAt;
  const attendanceStamp = status?.attendance?.updatedAt;
  const eclassStamp = `${status?.eclass?.updatedAt ?? ""}|${status?.sync.finished_at ?? ""}|${status?.eclass?.feed?.total ?? ""}`;
  useEffect(() => {
    void Promise.resolve().then(load);
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, [load, academicStamp, attendanceStamp, eclassStamp]);

  const items = data?.items ?? [];
  const unread = data?.unread ?? 0;
  const over = data?.bundleOver ?? 3;
  const missed = groupByTime(items.filter((n) => n.missed && !n.read), over);
  const recent = groupByTime(items.filter((n) => !(n.missed && !n.read)), over);

  const markRead = (ids: string[]) => {
    setData((d) => (d ? { ...d, items: d.items.map((n) => (ids.includes(n.id) ? { ...n, read: true } : n)), unread: Math.max(0, d.unread - ids.filter((id) => d.items.find((n) => n.id === id && !n.read)).length) } : d));
    void Promise.all(ids.map((id) => api.readNotification(id))).catch(() => load());
  };
  const markAll = () => {
    setData((d) => (d ? { ...d, items: d.items.map((n) => ({ ...n, read: true })), unread: 0 } : d));
    void api.readAllNotifications().catch(() => load());
  };

  const Row = ({ n, close }: { n: AppNotification; close: () => void }) => (
    <li>
      <button
        type="button"
        className="flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-surface-2"
        onClick={() => {
          if (!n.read) markRead([n.id]);
          close();
          if (n.href) router.push(n.href);
        }}
      >
        <span className={`mt-0.5 grid size-7 flex-none place-items-center rounded-lg [&>svg]:size-3.5 ${n.read ? "bg-surface-3 text-faint" : "bg-primary-soft text-primary"}`}>
          {KIND_ICON[n.kind] ?? <Bell aria-hidden />}
        </span>
        <span className="min-w-0 flex-1">
          <span className={`block text-[14px] leading-snug ${n.read ? "text-muted" : "font-semibold text-text"}`}>{n.title}</span>
          {n.body && <span className="block truncate text-[12px] text-muted">{n.body}</span>}
          <span className="num text-[12px] text-faint">{fmtRelative(parseLocal(n.at))}</span>
        </span>
        {!n.read && <span className="mt-2 size-2 flex-none rounded-full bg-accent" aria-label="안 읽음" />}
      </button>
    </li>
  );

  const GroupRows = ({ groups, close }: { groups: Group[]; close: () => void }) => (
    <ul>
      {groups.map((g) =>
        g.items.length === 1 ? (
          <Row key={g.key} n={g.items[0]} close={close} />
        ) : (
          <li key={g.key}>
            <button
              type="button"
              aria-expanded={!!openGroups[g.key]}
              className="flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-surface-2"
              onClick={() => setOpenGroups((m) => ({ ...m, [g.key]: !m[g.key] }))}
            >
              <span className="mt-0.5 grid size-7 flex-none place-items-center rounded-lg bg-primary-soft text-primary [&>svg]:size-3.5">
                <Landmark aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[14px] font-semibold leading-snug">학사 일정 {g.items.length}건</span>
                <span className="block truncate text-[12px] text-muted">{g.items[0].title} 외</span>
              </span>
              <ChevronRight className={`mt-1.5 size-4 flex-none text-faint transition-transform ${openGroups[g.key] ? "rotate-90" : ""}`} aria-hidden />
            </button>
            {openGroups[g.key] && (
              <ul className="ml-6 border-l border-border pl-1">
                {g.items.map((n) => (
                  <Row key={n.id} n={n} close={close} />
                ))}
              </ul>
            )}
          </li>
        ),
      )}
    </ul>
  );

  return (
    <Popover
      label="알림"
      width={380}
      trigger={({ open, toggle, ref }) => (
        <button
          ref={ref}
          type="button"
          className="btn btn-ghost btn-icon relative"
          aria-label={`알림${unread ? ` ${unread}건 안 읽음` : ""}`}
          aria-expanded={open}
          onClick={toggle}
        >
          <Bell className="size-[18px]" aria-hidden />
          <AnimatePresence>
            {unread > 0 && (
              <motion.span
                key={unread}
                initial={{ scale: 0.5, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.5, opacity: 0 }}
                className="num absolute top-1 right-1 grid min-w-[18px] place-items-center rounded-full bg-accent-text px-1 text-[11px] leading-[18px] font-bold text-on-accent"
              >
                {unread}
              </motion.span>
            )}
          </AnimatePresence>
        </button>
      )}
    >
      {(close) => (
        <div className="py-2">
          <div className="flex items-center justify-between px-4 pt-1 pb-2">
            <span className="text-[15px] font-bold">알림</span>
            <span className="text-[11px] font-semibold text-faint">학사일정</span>
          </div>
          {failed && !data ? (
            <p className="px-4 py-8 text-center text-[14px] text-danger-text">알림을 불러오지 못했습니다</p>
          ) : items.length === 0 ? (
            <p className="px-4 py-8 text-center text-[14px] text-faint">새 알림이 없습니다</p>
          ) : (
            <div className="thin-scroll max-h-[60vh] overflow-y-auto px-1">
              {missed.length > 0 && (
                <>
                  <p className="flex items-center gap-1.5 px-3 pt-1 pb-1 text-[12px] font-bold text-warn-text">
                    <TriangleAlert className="size-3.5" aria-hidden />
                    놓친 알림 {missed.reduce((s, g) => s + g.items.length, 0)} <span className="font-medium text-faint">· PC 가 꺼져 있던 동안</span>
                  </p>
                  <GroupRows groups={missed} close={close} />
                </>
              )}
              {recent.length > 0 && (
                <>
                  <p className="px-3 pt-2 pb-1 text-[12px] font-bold text-faint">최근</p>
                  <GroupRows groups={recent} close={close} />
                </>
              )}
            </div>
          )}
          <div className="mt-1 flex items-center justify-between border-t border-border px-3 pt-2">
            <button type="button" className="btn btn-ghost btn-sm" onClick={markAll} disabled={unread === 0}>
              모두 읽음
            </button>
            <Link href="/settings/notifications" className="btn btn-ghost btn-sm" onClick={close}>
              알림 설정
              <ChevronRight aria-hidden />
            </Link>
          </div>
        </div>
      )}
    </Popover>
  );
}

function SettingsMenu() {
  return (
    <Popover
      label="설정"
      width={220}
      trigger={({ open, toggle, ref }) => (
        <button ref={ref} type="button" className="btn btn-ghost btn-icon" aria-label="설정" aria-expanded={open} onClick={toggle}>
          <Settings className="size-[18px]" aria-hidden />
        </button>
      )}
    >
      {(close) => (
        <nav aria-label="설정" className="p-1.5">
          {SETTINGS.map((s) => (
            <Link
              key={s.href}
              href={s.href}
              onClick={close}
              className="flex h-10 items-center rounded-lg px-3 text-[14px] font-medium text-text transition-colors hover:bg-surface-2"
            >
              {s.label}
            </Link>
          ))}
        </nav>
      )}
    </Popover>
  );
}
