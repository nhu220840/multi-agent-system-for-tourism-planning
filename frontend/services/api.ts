const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000/api";
const CHAT_REQUEST_TIMEOUT_MS = Number.parseInt(process.env.NEXT_PUBLIC_CHAT_TIMEOUT_MS || "180000", 10);

export type Principal = {
  id: string;
  type: "anonymous" | "user";
  display_name?: string;
};

export type SessionInfo = {
  principal: Principal;
  session_started_at: string;
  last_seen_at: string;
};

export type MessagePayload = {
  id: string;
  role: string;
  content: string;
  metadata?: Record<string, unknown> | null;
  created_at: string;
};

export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  latest_message_preview?: string | null;
  message_count: number;
};

export type ConversationDetail = ConversationSummary & {
  principal_id: string;
  messages: MessagePayload[];
};

export type ChatRequest = {
  message: string;
  conversation_id?: string;
};

export type ChatResponse = {
  answer: string;
  conversation_id?: string | null;
  trace: string[];
  debug_steps?: DebugStep[];
  conversation_stage?: string;
  follow_up_questions?: string[] | null;
  plan?: string | null;
  route_plan?: Record<string, unknown>[] | null;
  stay_recommendations?: Record<string, unknown>[] | null;
  research?: string | null;
};

export type DebugStep = {
  key: string;
  title: string;
  status: string;
  summary: string;
  details: Record<string, unknown>;
};

async function parseJson<T>(response: Response, errorPrefix: string): Promise<T> {
  if (!response.ok) {
    throw new Error(`${errorPrefix}: ${response.status}`);
  }

  return (await response.json()) as T;
}

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const safeTimeout = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 90000;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), safeTimeout);
  try {
    return await fetch(input, {
      ...init,
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function initSession(): Promise<SessionInfo> {
  const response = await fetch(`${API_BASE}/session/init`, {
    method: "POST",
    credentials: "include",
  });

  return parseJson<SessionInfo>(response, "Session init failed");
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await fetch(`${API_BASE}/conversations`, {
    method: "GET",
    credentials: "include",
  });

  return parseJson<ConversationSummary[]>(response, "Conversation list failed");
}

export async function getConversation(conversationId: string): Promise<ConversationDetail> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
    method: "GET",
    credentials: "include",
  });

  return parseJson<ConversationDetail>(response, "Conversation lookup failed");
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
    method: "DELETE",
    credentials: "include",
  });

  if (!response.ok) {
    throw new Error(`Conversation delete failed: ${response.status}`);
  }
}

export async function deleteAllConversations(): Promise<{ deleted_conversations: number }> {
  const response = await fetch(`${API_BASE}/conversations`, {
    method: "DELETE",
    credentials: "include",
  });

  return parseJson<{ deleted_conversations: number }>(response, "Conversation reset failed");
}

export async function sendChat(message: string, conversationId?: string): Promise<ChatResponse> {
  await initSession();

  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${API_BASE}/chat/send`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          conversation_id: conversationId,
        } satisfies ChatRequest),
      },
      CHAT_REQUEST_TIMEOUT_MS,
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`Chat request timed out after ${Math.round(CHAT_REQUEST_TIMEOUT_MS / 1000)}s`);
    }
    throw error;
  }

  return parseJson<ChatResponse>(response, "Chat request failed");
}
