"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, FileText, KeyRound, MessageCircle, Send, Trash2, TriangleAlert } from "lucide-react";
import { Page, PageHeader } from "@/components/ui/Layout";
import { Banner } from "@/components/ui/Feedback";
import { Chip } from "@/components/ui/Chip";
import { useQueryValue } from "@/lib/useQueryState";
import { assistantApi, type LinkCard, type LlmSettings, type MaterialCard, type Pending, type SourceCard } from "@/lib/assistant";

// /chat — 내 데이터로 답하는 대화 (CA2 대화 엔진 + 이 PC 에 모아 둔 학사일정·과제·시험·출결·졸업 학점·강의자료 도구).
// 내 AI API 키를 넣으면 켜진다. 강의자료 본문은 동의한 뒤에만 AI 로 나간다. 쓰기(알림 예약)는 미리보기 카드를 확인해야만 실행된다.
// 대화 기록은 이 화면에만 있다.

type Msg =
  | { id: string; role: "user"; text: string }
  | {
      id: string;
      role: "assistant";
      text: string;
      status?: string;
      cards: SourceCard[];
      materials: MaterialCard[];
      links: LinkCard[];
      consent?: { text: string; question: string };
      notices: string[];
      pending: (Pending & { state: "pending" | "confirmed" | "canceled" })[];
      error?: string;
      warn?: { text: string; question: string };
    };

const EXAMPLES = ["이번 주 마감 뭐 남았어?", "다음 시험 언제야?", "졸업까지 몇 학점 남았어?"];
const TOOL_LABEL: Record<string, string> = {
  search_campus_info: "자료를 찾는 중…",
  create_reminder: "알림 미리보기를 만드는 중…",
  get_schedule: "캘린더를 보는 중…",
  get_assignments: "과제를 보는 중…",
  get_exams: "시험 일정을 보는 중…",
  get_attendance: "출결을 보는 중…",
  get_graduation: "졸업요건을 보는 중…",
  list_materials: "강의자료 목록을 보는 중…",
  search_materials: "강의자료를 훑는 중…",
};

export default function ChatPage() {
  const q = useQueryValue("q");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const sentQ = useRef(false);
  const history = useRef<{ role: "user" | "assistant"; content: string }[]>([]);

  useEffect(() => {
    assistantApi.settings().then(setSettings).catch(() => setSettings(null));
  }, []);

  const send = async (question: string, confirmPii = false) => {
    if (busy || !question.trim()) return;
    setBusy(true);
    setText("");
    const aid = `a${Date.now()}`;
    if (!confirmPii) history.current.push({ role: "user", content: question });
    setMsgs((m) => [
      ...(confirmPii ? m.filter((x) => !(x.role === "assistant" && x.warn)) : [...m, { id: `u${Date.now()}`, role: "user" as const, text: question }]),
      { id: aid, role: "assistant", text: "", cards: [], materials: [], links: [], notices: [], pending: [] },
    ]);
    const patch = (f: (a: Extract<Msg, { role: "assistant" }>) => Partial<Extract<Msg, { role: "assistant" }>>) =>
      setMsgs((m) => m.map((x) => (x.id === aid && x.role === "assistant" ? { ...x, ...f(x) } : x)));
    let answer = "";
    try {
      await assistantApi.chat(history.current.slice(-12), confirmPii, (e) => {
        if (e.event === "delta") {
          answer += e.data;
          patch(() => ({ text: answer }));
        } else if (e.event === "tool") patch(() => ({ status: TOOL_LABEL[e.data.name] ?? "처리 중…" }));
        else if (e.event === "card" && e.data.type === "source") {
          const card = e.data.data;
          patch((a) => ({ cards: [...a.cards, card] }));
        } else if (e.event === "card" && e.data.type === "material") {
          const card = e.data.data;
          patch((a) => ({ materials: [...a.materials, card] }));
        } else if (e.event === "card" && e.data.type === "link") {
          const link = e.data.data;
          patch((a) => (a.links.some((l) => l.href === link.href) ? {} : { links: [...a.links, link] }));
        } else if (e.event === "card" && e.data.type === "notice") {
          const notice = e.data.data.text;
          patch((a) => ({ notices: [...a.notices, notice] }));
        } else if (e.event === "consent") patch(() => ({ consent: { text: e.data.text, question } }));
        else if (e.event === "pending") patch((a) => ({ pending: [...a.pending, { ...e.data, state: "pending" as const }] }));
        else if (e.event === "error") patch(() => ({ error: e.data }));
        else if (e.event === "warn") patch(() => ({ warn: { text: e.data.text, question } }));
        else if (e.event === "mode") setSettings((s) => (s ? { ...s, configured: false } : s));
      });
    } catch (err) {
      patch(() => ({ error: err instanceof Error ? err.message : "답하지 못했습니다" }));
    }
    patch(() => ({ status: undefined }));
    if (answer) history.current.push({ role: "assistant", content: answer });
    setBusy(false);
  };

  useEffect(() => {
    if (q && !sentQ.current) {
      sentQ.current = true;
      void send(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [msgs]);

  const clearChat = () => {
    history.current = [];
    setMsgs([]);
  };

  return (
    <Page>
      <PageHeader
        icon={<MessageCircle />}
        title="대화"
        actions={
          <>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowSettings((v) => !v)} aria-expanded={showSettings}>
              <KeyRound aria-hidden />
              AI 연결 설정
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={clearChat}>
              <Trash2 aria-hidden />
              대화 지우기
            </button>
          </>
        }
      />
      {settings && !settings.configured && (
        <Banner tone="warn" className="mb-4" icon={<TriangleAlert aria-hidden />}>
          AI 가 연결되지 않았습니다. ‘AI 연결 설정’에서 내 API 키를 넣으면 대화가 켜집니다.
        </Banner>
      )}
      {showSettings && settings && <SettingsPanel value={settings} onChange={setSettings} />}
      <div className="card flex min-h-[60vh] flex-col">
        <div className="flex-1 space-y-3 p-4 md:p-6" aria-live="polite">
          {msgs.length === 0 && (
            <div className="py-10 text-center">
              <p className="mb-3 text-[14px] text-muted">내 일정·과제·시험·출결·졸업 학점·강의자료를 물어보세요</p>
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((e) => (
                  <button key={e} type="button" className="btn btn-sm" onClick={() => void send(e)}>
                    {e}
                  </button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m) => (
            <Message
              key={m.id}
              m={m}
              onPending={(pid, state) =>
                setMsgs((xs) =>
                  xs.map((x) => (x.id === m.id && x.role === "assistant" ? { ...x, pending: x.pending.map((p) => (p.id === pid ? { ...p, state } : p)) } : x)),
                )
              }
              onConsent={async (agree, question) => {
                setMsgs((xs) => xs.map((x) => (x.id === m.id && x.role === "assistant" ? { ...x, consent: undefined } : x)));
                if (!agree) return;
                await assistantApi.setConsent(true);
                void send(question);
              }}
              onWarn={(sendAnyway, question) => {
                if (sendAnyway) void send(question, true);
                else {
                  history.current.pop();
                  setMsgs((xs) => xs.filter((x) => x.id !== m.id));
                }
              }}
            />
          ))}
          <div ref={end} />
        </div>
        <form
          className="sticky bottom-0 flex gap-2 rounded-b-xl border-t border-border bg-surface p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void send(text.trim());
          }}
        >
          <label htmlFor="chat-in" className="sr-only">
            메시지
          </label>
          <textarea
            id="chat-in"
            rows={1}
            className="field min-h-[42px] flex-1 resize-none py-2.5"
            placeholder="예) 운영체제 5주차 강의자료에서 교착 상태 설명해 줘"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send(text.trim());
              }
            }}
          />
          <button type="submit" className="btn btn-primary btn-icon" aria-label="보내기" disabled={busy || !text.trim()}>
            <Send aria-hidden />
          </button>
        </form>
      </div>
    </Page>
  );
}

function Message({
  m,
  onPending,
  onConsent,
  onWarn,
}: {
  m: Msg;
  onPending: (id: string, state: "confirmed" | "canceled") => void;
  onConsent: (agree: boolean, question: string) => void;
  onWarn: (sendAnyway: boolean, question: string) => void;
}) {
  if (m.role === "user")
    return <p className="ml-auto w-fit max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-primary px-4 py-2 text-[14px] text-on-primary">{m.text}</p>;
  const waiting = !m.text && !m.error && !m.warn;
  return (
    <div className="max-w-[85%] space-y-2">
      {m.status && <p className="px-1 text-[12px] text-faint">{m.status}</p>}
      {m.warn ? (
        <div className="rounded-2xl border border-border bg-surface p-4 text-[14px]">
          <p>{m.warn.text}</p>
          <div className="mt-3 flex gap-2">
            <button type="button" className="btn btn-primary btn-sm" onClick={() => onWarn(false, m.warn!.question)}>
              보내지 않기
            </button>
            <button type="button" className="btn btn-sm" onClick={() => onWarn(true, m.warn!.question)}>
              그래도 보내기
            </button>
          </div>
        </div>
      ) : (
        <p className="w-fit whitespace-pre-wrap rounded-2xl rounded-bl-md bg-surface-3 px-4 py-3 text-[14px]">
          {waiting ? "…" : m.text || "답하지 못했습니다"}
        </p>
      )}
      {m.error && <p className="px-1 text-[13px] text-danger">{m.error}</p>}
      {m.notices.map((n, i) => (
        <p key={i} className="px-1 text-[13px] text-muted">
          {n}
        </p>
      ))}
      {m.cards.map((c, i) => (
        <div key={i} className="rounded-xl border border-border bg-surface p-3 text-[13px]">
          <p className="flex flex-wrap items-center gap-2 font-semibold">
            {c.title} · {c.section}
            {c.stale && <Chip tone="warn">정보가 바뀌었을 수 있어요</Chip>}
          </p>
          <p className="text-faint">
            {c.dept || "담당 부서 미상"} · 검증일 {c.verifiedAt || "없음"}
          </p>
          <p className="mt-1.5 whitespace-pre-wrap">{c.text}</p>
          {/^https?:\/\//.test(c.sourceUrl) && (
            <a href={c.sourceUrl} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block font-semibold text-primary hover:underline">
              원문 열기
            </a>
          )}
        </div>
      ))}
      {m.materials.map((c, i) => (
        <a
          key={i}
          href={c.href}
          target="_blank"
          rel="noopener noreferrer"
          className="block rounded-xl border border-border bg-surface p-3 text-[13px] hover:border-primary"
        >
          <span className="flex flex-wrap items-center gap-2 font-semibold">
            <FileText className="size-4 text-faint" aria-hidden />
            {c.title}
            <Chip tone="primary">p.{c.page}</Chip>
          </span>
          <span className="block text-faint">{c.course}</span>
          <span className="mt-1.5 block whitespace-pre-wrap text-muted">{c.text}…</span>
        </a>
      ))}
      {m.links.length > 0 && (
        <p className="flex flex-wrap gap-3 px-1 text-[13px]">
          {m.links.map((l) => (
            <Link key={l.href} href={l.href} className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">
              {l.label}
              <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          ))}
        </p>
      )}
      {m.consent && (
        <div className="rounded-2xl border-2 border-primary bg-surface p-4 text-[14px]">
          <p className="mb-1 font-bold">강의자료를 AI 로 보내도 될까요?</p>
          <p>{m.consent.text}</p>
          <p className="mt-1 text-[12px] text-faint">한 번 동의하면 다시 묻지 않습니다. ‘AI 연결 설정’에서 언제든 끌 수 있습니다.</p>
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" className="btn btn-sm" onClick={() => onConsent(false, m.consent!.question)}>
              보내지 않기
            </button>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => onConsent(true, m.consent!.question)}>
              동의하고 다시 묻기
            </button>
          </div>
        </div>
      )}
      {m.pending.map((p) => {
        const expired = p.state === "pending" && new Date(p.expiresAt) < new Date();
        const done = p.state !== "pending" || expired;
        return (
          <div key={p.id} className={`rounded-2xl border-2 bg-surface p-4 text-[14px] ${done ? "border-border opacity-70" : "border-primary"}`}>
            <p className="mb-1 flex items-center gap-2 font-bold">
              이렇게 알릴까요?
              {p.state === "confirmed" && <Chip tone="ok">확인함</Chip>}
              {p.state === "canceled" && <Chip>취소함</Chip>}
              {expired && <Chip>만료됨 — 다시 말씀해 주세요</Chip>}
            </p>
            <p>{p.preview}</p>
            <p className="mt-1 text-[12px] text-faint">
              {p.state === "confirmed" ? "프로토타입 — 실제 알림 발송은 아직 연결되지 않았습니다" : "확인하기 전에는 예약되지 않습니다"}
            </p>
            {!done && (
              <div className="mt-3 flex justify-end gap-2">
                <button type="button" className="btn btn-sm" onClick={() => onPending(p.id, "canceled")}>
                  취소
                </button>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => onPending(p.id, "confirmed")}>
                  확인
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SettingsPanel({ value, onChange }: { value: LlmSettings; onChange: (v: LlmSettings) => void }) {
  const first = value.providers[0];
  const [provider, setProvider] = useState(value.provider || first.key);
  const [baseUrl, setBaseUrl] = useState(value.baseUrl || first.baseUrl);
  const [model, setModel] = useState(value.model);
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const hint = value.providers.find((p) => p.key === provider)?.modelHint ?? "모델 이름";

  const run = async (label: string, f: () => Promise<string>) => {
    setNote({ text: label });
    try {
      setNote({ text: await f() });
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : "실패했습니다", bad: true });
    }
  };

  return (
    <section className="card mb-4 p-4 md:p-6" aria-label="AI 연결 설정">
      <h2 className="text-[15px] font-bold">AI 연결 설정</h2>
      <p className="mt-1 text-[13px] text-muted">
        내 API 키는 이 PC 의 암호 저장소에만 저장됩니다. 질문 문장은 고른 AI 회사로 전송됩니다(‘내 PC’ 를 고르면 밖으로 나가지 않습니다).
      </p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="text-[13px] text-muted">
          제공자
          <select
            className="field mt-1"
            value={provider}
            onChange={(e) => {
              const p = value.providers.find((x) => x.key === e.target.value);
              setProvider(e.target.value);
              setBaseUrl(p?.baseUrl ?? "");
              setModel("");
              setModels([]);
            }}
          >
            {value.providers.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[13px] text-muted">
          주소
          <input className="field mt-1" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…/v1" autoComplete="off" />
        </label>
        <label className="text-[13px] text-muted">
          API 키 {value.source === "settings" && value.keyHint ? `(저장됨 ${value.keyHint})` : value.source === "env" ? "(.env 의 키 사용 중)" : ""}
          <input
            className="field mt-1"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="새 키를 넣을 때만 입력"
            autoComplete="off"
          />
        </label>
        <label className="text-[13px] text-muted">
          모델
          <input className="field mt-1" value={model} onChange={(e) => setModel(e.target.value)} placeholder={hint} list="assistant-models" autoComplete="off" />
          <datalist id="assistant-models">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </label>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          className="btn btn-sm"
          onClick={() =>
            run("모델 목록을 불러오는 중…", async () => {
              const r = await assistantApi.models(baseUrl, apiKey);
              setModels(r.models);
              return `모델 ${r.models.length}개를 찾았습니다. 모델 칸에서 골라 주세요.`;
            })
          }
        >
          모델 목록 불러오기
        </button>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() =>
            run("저장하는 중…", async () => {
              onChange(await assistantApi.save({ provider, baseUrl, model, apiKey: apiKey || null }));
              setApiKey("");
              return "저장했습니다. ‘연결 시험’으로 확인해 보세요.";
            })
          }
        >
          저장
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() =>
            run("연결을 시험하는 중…", async () => {
              const r = await assistantApi.test();
              if (!r.ok) throw new Error(`연결 실패: ${r.error}`);
              return r.tools ? "연결됐습니다. 도구 호출도 됩니다." : "연결됐습니다. 다만 이 모델은 도구 호출을 하지 않아 답이 부정확할 수 있습니다.";
            })
          }
        >
          연결 시험
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() =>
            run("지우는 중…", async () => {
              onChange(await assistantApi.clear());
              setModel("");
              return "설정과 키를 지웠습니다.";
            })
          }
        >
          지우기
        </button>
      </div>
      <label className="mt-4 flex items-start gap-2 text-[13px]">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={value.materialsConsent}
          onChange={(e) =>
            run("바꾸는 중…", async () => {
              const r = await assistantApi.setConsent(e.target.checked);
              onChange({ ...value, materialsConsent: r.materials });
              return r.materials ? "강의자료 전송에 동의했습니다." : "강의자료를 AI 로 보내지 않습니다.";
            })
          }
        />
        <span>
          강의자료 본문을 AI 로 보내는 데 동의합니다
          <span className="block text-faint">파일은 이 PC 에 남고, 질문과 관련된 쪽의 글자 일부만 전송됩니다. ‘내 PC’ 모델이면 동의 없이도 밖으로 나가지 않습니다.</span>
        </span>
      </label>
      {note && (
        <p role="status" className={`mt-3 text-[13px] ${note.bad ? "text-danger" : "text-muted"}`}>
          {note.text}
        </p>
      )}
    </section>
  );
}
