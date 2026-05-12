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

export type ChatStageName = "intake" | "planning" | "validator" | "response";

export type ChatStreamConversationEvent = {
  conversation_id: string | null;
};

export type ChatStreamStageEvent = {
  stage: ChatStageName;
  status: "started" | "completed";
  trace: string[];
  conversation_stage?: string | null;
  missing_fields?: string[];
  follow_up_questions?: string[];
  needs_replan?: boolean;
  retrying?: boolean;
};

type SendChatStreamHandlers = {
  onConversation?: (event: ChatStreamConversationEvent) => void;
  onStage?: (event: ChatStreamStageEvent) => void;
  onAnswerStart?: (event: ChatStreamConversationEvent) => void;
  onAnswerDelta?: (delta: string) => void;
  onComplete?: (response: ChatResponse) => void;
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

function parseSseBlock(block: string): { event: string; data: string } | null {
  const trimmed = block.trim();
  if (!trimmed) {
    return null;
  }

  let event = "message";
  const dataLines: string[] = [];

  for (const line of trimmed.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  return {
    event,
    data: dataLines.join("\n"),
  };
}

export async function sendChatStream(
  message: string,
  conversationId: string | undefined,
  handlers: SendChatStreamHandlers,
): Promise<ChatResponse> {
  await initSession();

  let response: Response;
  try {
    response = await fetchWithTimeout(
      `${API_BASE}/chat/stream`,
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

  if (!response.ok) {
    throw new Error(`Chat request failed: ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Streaming response is not available in this browser.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse: ChatResponse | null = null;
  let streamError: string | null = null;

  const handleBlock = (block: string) => {
    const parsed = parseSseBlock(block);
    if (!parsed) {
      return;
    }

    const payload = JSON.parse(parsed.data) as Record<string, unknown>;
    switch (parsed.event) {
      case "conversation":
        handlers.onConversation?.(payload as unknown as ChatStreamConversationEvent);
        break;
      case "stage":
        handlers.onStage?.(payload as unknown as ChatStreamStageEvent);
        break;
      case "answer_start":
        handlers.onAnswerStart?.(payload as unknown as ChatStreamConversationEvent);
        break;
      case "answer_delta":
        handlers.onAnswerDelta?.(String(payload.delta || ""));
        break;
      case "complete":
        finalResponse = payload as unknown as ChatResponse;
        handlers.onComplete?.(finalResponse);
        break;
      case "error":
        streamError = String(payload.message || "Streaming chat failed.");
        break;
      default:
        break;
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

    while (true) {
      const delimiterIndex = buffer.indexOf("\n\n");
      if (delimiterIndex < 0) {
        break;
      }
      const block = buffer.slice(0, delimiterIndex);
      buffer = buffer.slice(delimiterIndex + 2);
      handleBlock(block);
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    handleBlock(buffer);
  }

  if (streamError) {
    throw new Error(streamError);
  }
  if (!finalResponse) {
    throw new Error("Chat stream ended before the final response arrived.");
  }

  return finalResponse;
}
