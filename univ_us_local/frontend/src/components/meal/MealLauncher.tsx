"use client";

import Image from "next/image";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { useStored } from "@/lib/storage";
import styles from "./MealLauncher.module.css";

const TRANSPARENCY_KEY = "meal.launcher-transparency";

// CA1의 화면과 상태를 그대로 보존한다. 앱의 학사 페이지에는 버튼만 추가한다.
export default function MealLauncher() {
  const pathname = usePathname();
  const dialog = useRef<HTMLDialogElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const [opened, setOpened] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [savedTransparency, saveTransparency] = useStored<number>(TRANSPARENCY_KEY, 0);
  // 저장소가 차단돼 있어도 현재 창에서 조절할 수 있다.
  const [sessionTransparency, setSessionTransparency] = useState<number | null>(null);
  const stored = typeof savedTransparency === "number" && Number.isFinite(savedTransparency) ? savedTransparency : 0;
  const transparency = sessionTransparency ?? Math.min(80, Math.max(0, stored));

  useEffect(() => {
    if (!opened) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [opened]);

  function open() {
    setLoaded(true);
    setOpened(true);
    dialog.current?.showModal();
  }

  function close() {
    dialog.current?.close();
  }

  function changeTransparency(value: number) {
    setSessionTransparency(value);
    saveTransparency(value);
  }

  const opacity = 1 - transparency / 100;

  return (
    <>
      <button
        ref={launcher}
        type="button"
        className={`${styles.launcher} ${pathname === "/chat" || pathname === "/chat/" ? styles.aboveComposer : ""}`}
        style={{ opacity }}
        onClick={open}
        aria-label="전룡이 식당 추천 열기"
        aria-haspopup="dialog"
        aria-expanded={opened}
        aria-controls="meal-recommendation-window"
        title="전룡이와 식당 고르기"
      >
        <Image src="/images/jeonryong-spoon-fork.webp" alt="" width={84} height={84} unoptimized />
      </button>
      <dialog
        ref={dialog}
        id="meal-recommendation-window"
        className={styles.dialog}
        aria-labelledby="meal-window-title"
        onClose={() => {
          setOpened(false);
          launcher.current?.focus();
        }}
        onClick={(event) => {
          // 실제 backdrop 클릭만 닫는다. 내부 입력·지도 클릭은 유지한다.
          if (event.target !== event.currentTarget) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
        }}
      >
        <div className={styles.content}>
          <header className={styles.toolbar}>
            <h2 id="meal-window-title">식당 추천</h2>
            <label className={styles.transparency}>
              <Image src="/images/jeonryong-spoon-fork.webp" alt="" width={32} height={32} unoptimized style={{ opacity }} />
              <span>전룡이 투명도</span>
              <input
                type="range"
                min="0"
                max="80"
                step="5"
                value={transparency}
                onChange={(event) => changeTransparency(Number(event.target.value))}
                aria-label="전룡이 투명도"
                aria-valuetext={`${transparency}% 투명`}
              />
              <output>{transparency}%</output>
            </label>
            <button type="button" className={styles.close} onClick={close} aria-label="식당 추천 닫기" autoFocus>
              <X size={22} aria-hidden />
            </button>
          </header>
          {loaded && <iframe className={styles.frame} src="/api/meal/" title="기존 식당 추천 화면" />}
        </div>
      </dialog>
    </>
  );
}
