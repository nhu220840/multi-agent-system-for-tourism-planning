export type ChatRequest = {
  message: string;
};

export type ChatResponse = {
  answer: string;
  trace: string[];
};

export async function sendChat(message: string): Promise<ChatResponse> {
  const res = await fetch("http://localhost:8000/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message } satisfies ChatRequest),
  });

  if (!res.ok) {
    throw new Error(`Chat request failed: ${res.status}`);
  }

  return (await res.json()) as ChatResponse;
}
