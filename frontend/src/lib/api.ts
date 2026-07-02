import { supabase } from "@/integrations/supabase/client";

export const API_BASE = (import.meta.env.VITE_API_BASE as string) || "http://localhost:8001";

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function apiGet<T = any>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: { ...(await authHeaders()) } });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export async function apiPost<T = any>(path: string, body?: any): Promise<T> {
  const isForm = body instanceof FormData;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      ...(await authHeaders()),
      ...(isForm ? {} : { "Content-Type": "application/json" }),
    },
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// ---------- High-level API helpers ----------

export interface AskResponse {
  answer: string;
  references?: any[];
  [k: string]: any;
}

export interface HistoryMessage {
  id: string;
  role: "user" | "ai";
  content: string;
}

export const ask = (question: string, session_id: string) =>
  apiPost<AskResponse>("/ask", { question, session_id });

// ── Original HTTP streaming — kept intact for any other callers ───────────────
export async function askStream(
  question: string,
  session_id: string,
  onToken: (token: string) => void,
  history: HistoryMessage[] = [],
): Promise<void> {
  const headers = {
    ...(await authHeaders()),
    "Content-Type": "application/json",
  };

  const mappedHistory = history
    .filter(m => m.content.trim() !== "")
    .map(m => ({
      role: m.role === "ai" ? "model" : "user",
      content: m.content,
    }));

  const res = await fetch(`${API_BASE}/ask/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, session_id, history: mappedHistory }),
  });

  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onToken(decoder.decode(value));
  }
}

// ── SSE chat streaming — replaces askStreamWs ─────────────────────────────────
//
// Consumes the SSE events produced by /ask/stream (text/event-stream).
// Event types:
//   {"type": "widget_chunk", "text": "..."}   — incremental widget HTML (ignored for display, kept for onToken)
//   {"type": "done", "widget": "...", "teacher": "..."}  — final split output
//   {"type": "stream_end"}                     — generation complete
//   {"type": "error", "message": "..."}        — server error

export interface AskStreamSseCallbacks {
  onToken  : (token: string) => void;
  onDone   : (widget: string, teacher: string) => void;
  onError? : (message: string) => void;
}

export async function askStreamSse(
  question  : string,
  session_id: string,
  history   : HistoryMessage[],
  callbacks : AskStreamSseCallbacks,
): Promise<void> {
  const headers = {
    ...(await authHeaders()),
    "Content-Type": "application/json",
  };

  const mappedHistory = history
    .filter(m => m.content.trim() !== "")
    .map(m => ({
      role   : m.role === "ai" ? "model" : "user",
      content: m.content,
    }));

  const res = await fetch(`${API_BASE}/ask/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, session_id, history: mappedHistory }),
  });

  if (!res.ok) {
    const text = await res.text();
    callbacks.onError?.(`${res.status} ${text}`);
    throw new Error(`${res.status} ${text}`);
  }

  const reader  = res.body!.getReader();
  const decoder = new TextDecoder();
  let   buffer  = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE lines are separated by double newline: "data: {...}\n\n"
    const parts = buffer.split("\n\n");
    // Keep the last incomplete chunk in the buffer
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;

      const jsonStr = line.slice("data:".length).trim();
      let msg: Record<string, any>;
      try {
        msg = JSON.parse(jsonStr);
      } catch {
        // Malformed line — skip
        continue;
      }

      switch (msg.type) {
        case "widget_chunk":
          // Raw partial widget HTML — passed to onToken so TypingIndicator
          // stays active; Chat.tsx intentionally ignores the token value.
          if (typeof msg.text === "string") {
            callbacks.onToken(msg.text);
          }
          break;

        case "done":
          callbacks.onDone(
            typeof msg.widget  === "string" ? msg.widget  : "",
            typeof msg.teacher === "string" ? msg.teacher : "",
          );
          break;

        case "stream_end":
          // Generation complete — nothing extra to do, Promise resolves below.
          break;

        case "error":
          callbacks.onError?.(
            typeof msg.message === "string" ? msg.message : "Unknown server error",
          );
          throw new Error(
            typeof msg.message === "string" ? msg.message : "Unknown server error",
          );

        default:
          break;
      }
    }
  }
}

// ── toGeminiHistory ───────────────────────────────────────────────────────────
export function toGeminiHistory(
  messages: Array<{ role: "user" | "ai"; content: string }>,
): Array<{ role: "user" | "model"; content: string }> {
  return messages
    .filter(m => m.content.trim() !== "")
    .map(m => ({
      role   : m.role === "ai" ? "model" as const : "user" as const,
      content: m.content,
    }));
}

export const uploadCourse = (file: File, extra?: Record<string, string>) => {
  const fd = new FormData();
  fd.append("file", file);
  if (extra) Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  return apiPost("/upload-course", fd);
};

export const uploadTest = (file: File, extra?: Record<string, string>) => {
  const fd = new FormData();
  fd.append("file", file);
  if (extra) Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  return apiPost("/upload-test", fd);
};

export const submitAnswers = (answers: any, session_id: string) =>
  apiPost("/submit-answers", { answers, session_id });

export const getResults = (session_id: string) =>
  apiGet(`/results/${session_id}`);

export const fetchReferences = (sources: any[], course_id = "tunisian_college") =>
  apiPost<{ references: any[] }>("/references", { sources, course_id });

export const AVATAR_WS_URL =
  (import.meta.env.VITE_AVATAR_WS as string) ||
  API_BASE.replace(/^http/, "ws") + "/ws/avatar";