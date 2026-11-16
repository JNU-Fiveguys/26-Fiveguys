"use client";

import type { ReactNode } from "react";
import { MotionConfig } from "motion/react";
import { ToastProvider } from "@/components/ui/Toast";
import { AppDataProvider } from "./AppData";
import Header from "./Header";
import MealLauncher from "@/components/meal/MealLauncher";

// 앱 공통 층: 움직임 줄이기 설정 존중(MotionConfig reducedMotion="user") · 토스트 · 실제 데이터 · 헤더.
export default function Providers({ children }: { children: ReactNode }) {
  return (
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <AppDataProvider>
          <Header />
          {children}
          <MealLauncher />
        </AppDataProvider>
      </ToastProvider>
    </MotionConfig>
  );
}
