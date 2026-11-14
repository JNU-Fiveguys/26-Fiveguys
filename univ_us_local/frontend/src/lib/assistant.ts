// CA2 캠퍼스 생활 Q&A·에이전트 대화 — campus/CA2_Assistant_agent 의 API(/api/assistant/v1/*) 를 부른다.
// 대화 기록은 이 화면(메모리)에만 둔다 — 서버는 원문을 저장하지 않는다.

const BASE = "/api/assistant/v1";

export type Provider = { key: string; label: string; baseUrl: string; modelHint?: string };
export type LlmSettings = {
  providers: Provider[];
  provider: string;
  baseUrl: string;
  model: string;
  source: "settings" | "env" | "none";
  configured: boolean;
  hasKey: boolean;
  keyHint: string;
  local: boolean;
  materialsConsent: boolean;
};
export type SourceCard = { title: string; section: string; dept: string; verifiedAt: string; sourceUrl: string; stale: boolean; text: string };
export type MaterialCard = { title: string; page: number; course: string; text: string; href: string };
export type LinkCard = { label: string; href: string };
export type Pending = { id: string; tool: string; args: { title: string; at: string }; preview: string; expiresAt: string };
export type ChatEvent =
  | { event: "delta"; data: string }
  | { event: "mode"; data: "simple" }
  | { event: "tool"; data: { name: string } }
  | {
      event: "card";
      data:
        | { type: "source"; data: SourceCard }
        | { type: "material"; data: MaterialCard }
        | { type: "link"; data: LinkCard }
        | { type: "notice"; data: { text: string } };
    }
  | { event: "consent"; data: { tool: string; text: string } }
  | { event: "pending"; data: Pending }
  | { event: "warn"; data: { kinds: string[]; text: string } }
  | { event: "error"; data: string }
  | { event: "done"; data: null };

async function req<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `오류 (${res.status})`);
  return data as T;
}

export const assistantApi = {
  settings: () => req<LlmSettings>("/settings/llm"),
  save: (s: { provider: string; baseUrl: string; model: string; apiKey: string | null }) => req<LlmSettings>("/settings/llm", "PUT", s),
  clear: () => req<LlmSettings>("/settings/llm", "DELETE"),
  models: (baseUrl: string, apiKey: string) => req<{ models: string[] }>("/settings/llm/models", "POST", { baseUrl, apiKey }),
  test: () => req<{ ok: boolean; tools: boolean; error: string | null }>("/settings/llm/test", "POST"),
  setConsent: (materials: boolean) => req<{ materials: boolean }>("/settings/consent", "PUT", { materials }),

  /** 대화 한 턴 — SSE 를 읽어 이벤트마다 on 을 부른다. */
  async chat(messages: { role: "user" | "assistant"; content: string }[], confirmPii: boolean, on: (e: ChatEvent) => void, signal?: AbortSignal) {
    const res = await fetch(`${BASE}/agent/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, confirmPii }),
      signal,
    });
    if (!res.ok || !res.body) throw new Error(`오류 (${res.status})`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i: number;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const event = /^event: (.*)$/m.exec(block)?.[1];
        const data = /^data: (.*)$/m.exec(block)?.[1];
        if (event && data !== undefined) on({ event, data: JSON.parse(data) } as ChatEvent);
      }
    }
  },
};
