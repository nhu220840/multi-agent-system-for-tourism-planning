"use client";

import { FormEvent, ReactNode, useEffect, useState, useTransition } from "react";

import {
  type ChatResponse,
  type ConversationDetail,
  type ConversationSummary,
  type DebugStep,
  type Principal,
  getConversation,
  initSession,
  listConversations,
  sendChat,
} from "@/services/api";

type DraftMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  metadata?: Record<string, unknown> | null;
  pending?: boolean;
};

type AssistantMessageMetadata = Partial<ChatResponse> & {
  collected_info?: Record<string, unknown> | null;
  recommended_hotel?: Record<string, unknown> | null;
};

type DaySlotSummary = {
  label: string;
  text: string;
};

type DaySummary = {
  title: string;
  theme: string;
  slots: DaySlotSummary[];
};

type StayRecommendation = {
  segment: string;
  name: string;
  priceNote: string;
  address: string;
  whyFit: string;
  mapUrl: string | null;
};

type PlannerSnapshot = {
  destination: string;
  daysLabel: string;
  hotelName: string;
  hotelMapUrl: string | null;
  stayRecommendations: StayRecommendation[];
  followUp: string | null;
  daySummaries: DaySummary[];
  hasPlan: boolean;
};

const URL_PATTERN = /https?:\/\/[^\s]+/g;

function toDraftMessages(conversation: ConversationDetail | null): DraftMessage[] {
  if (!conversation) {
    return [];
  }

  return conversation.messages.map((message) => ({
    id: message.id,
    role: message.role === "assistant" ? "assistant" : "user",
    content: message.content,
    metadata: message.metadata,
  }));
}

function normalizeTrace(trace: string[] | null | undefined): string[] {
  return Array.isArray(trace)
    ? trace.map((item) => String(item).trim()).filter(Boolean)
    : [];
}

function extractConversationSignals(conversation: ConversationDetail | null): {
  followUps: string[];
  trace: string[];
  debugSteps: DebugStep[];
} {
  if (!conversation) {
    return { followUps: [], trace: [], debugSteps: [] };
  }

  for (let index = conversation.messages.length - 1; index >= 0; index -= 1) {
    const message = conversation.messages[index];
    if (message.role !== "assistant" || !message.metadata) {
      continue;
    }

    const trace = normalizeTrace((message.metadata as { trace?: string[] }).trace);
    const followUps = Array.isArray((message.metadata as { follow_up_questions?: string[] }).follow_up_questions)
      ? ((message.metadata as { follow_up_questions?: string[] }).follow_up_questions || []).map((item) => String(item))
      : [];
    const debugSteps = Array.isArray((message.metadata as { debug_steps?: DebugStep[] }).debug_steps)
      ? ((message.metadata as { debug_steps?: DebugStep[] }).debug_steps || [])
      : [];
    return { followUps, trace, debugSteps };
  }

  return { followUps: [], trace: [], debugSteps: [] };
}

type PendingStepBlueprint = {
  key: string;
  title: string;
  durationMs: number;
  queuedSummary: string;
  runningSummary: string;
  doneSummary: string;
};

const PENDING_STEP_BLUEPRINTS: PendingStepBlueprint[] = [
  {
    key: "intake",
    title: "1. Intake Agent",
    durationMs: 900,
    queuedSummary: "Dang cho bat dau phan tich yeu cau.",
    runningSummary: "Dang phan tich yeu cau va trich xuat thong tin co cau truc.",
    doneSummary: "Da trich xuat xong input co cau truc va chuyen sang planning.",
  },
  {
    key: "planning",
    title: "2. Planning Agent",
    durationMs: 3200,
    queuedSummary: "Dang cho Intake Agent hoan tat.",
    runningSummary: "Dang goi retrieval, scoring, research va lap lich trong mot agent.",
    doneSummary: "Da tong hop retrieval, context, research va lich trinh nhap.",
  },
  {
    key: "validation",
    title: "3. Validator Agent",
    durationMs: 1200,
    queuedSummary: "Dang cho Planning Agent hoan tat.",
    runningSummary: "Dang kiem tra do day du cua lich trinh va quyet dinh co can replan hay khong.",
    doneSummary: "Da kiem tra xong validation va san sang tra ket qua.",
  },
  {
    key: "response",
    title: "4. Response Service",
    durationMs: 900,
    queuedSummary: "Dang cho validator xac nhan ket qua.",
    runningSummary: "Dang dinh dang cau tra loi cuoi cung cho UI.",
    doneSummary: "Da hoan tat response va sap cap nhat giao dien.",
  },
];

function stripStepNumber(title: string): string {
  return title.replace(/^\d+\.\s*/, "").trim();
}

function formatElapsedMs(ms: number): string {
  if (ms < 1000) {
    return `${Math.max(0, Math.round(ms))} ms`;
  }
  return `${(ms / 1000).toFixed(1)} s`;
}

function buildPendingDebugSteps(elapsedMs: number): DebugStep[] {
  let offsetMs = 0;
  const lastIndex = PENDING_STEP_BLUEPRINTS.length - 1;

  return PENDING_STEP_BLUEPRINTS.map((step, index) => {
    const startMs = offsetMs;
    const endMs = offsetMs + step.durationMs;
    offsetMs = endMs;

    let status: DebugStep["status"] = "queued";
    let summary = step.queuedSummary;
    let elapsedForStep = 0;

    if (index === lastIndex && elapsedMs >= startMs) {
      status = "running";
      summary = step.runningSummary;
      elapsedForStep = Math.max(0, elapsedMs - startMs);
    } else if (elapsedMs >= endMs) {
      status = "done";
      summary = step.doneSummary;
      elapsedForStep = step.durationMs;
    } else if (elapsedMs >= startMs) {
      status = "running";
      summary = step.runningSummary;
      elapsedForStep = Math.max(0, elapsedMs - startMs);
    }

    return {
      key: step.key,
      title: step.title,
      status,
      summary,
      details: {
        elapsed: formatElapsedMs(elapsedForStep),
        target: formatElapsedMs(step.durationMs),
      },
    };
  });
}

function buildTraceSteps(steps: DebugStep[]): Array<{ label: string; status: string }> {
  return steps.map((step) => ({
    label: stripStepNumber(step.title),
    status: step.status,
  }));
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    done: "Done",
    skipped: "Skipped",
    waiting: "Waiting",
    queued: "Queued",
    running: "Running",
    partial: "Partial",
    needs_input: "Needs input",
  };
  return labels[status] || status;
}

function formatDebugValue(value: unknown): string {
  if (value == null) {
    return "-";
  }
  if (typeof value === "string") {
    return value || "-";
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatRelativeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown";
  }

  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function extractFirstUrl(text: string): string | null {
  const match = text.match(URL_PATTERN);
  return match?.[0] || null;
}

function truncateMiddle(value: string, maxLength = 48): string {
  if (value.length <= maxLength) {
    return value;
  }
  const sideLength = Math.max(10, Math.floor((maxLength - 3) / 2));
  return `${value.slice(0, sideLength)}...${value.slice(-sideLength)}`;
}

function linkLabelForLine(url: string, line: string, index: number): string {
  const lower = line.toLowerCase();
  if (lower.includes("ban do tuyen ngay")) {
    return index > 0 ? `Mo tuyen ${index + 1}` : "Mo tuyen";
  }
  if (lower.includes("map tung chang")) {
    return `Map chang ${index + 1}`;
  }
  if (lower.includes(" map:")) {
    return index > 0 ? `Mo map ${index + 1}` : "Mo map";
  }
  if (lower.includes("directions")) {
    return "Chi duong";
  }

  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    if (host.includes("openstreetmap")) {
      return "OpenStreetMap";
    }
    return host;
  } catch {
    return truncateMiddle(url, 34);
  }
}

function cleanDisplayLine(line: string): string {
  let text = line.trim();
  if (!text) {
    return "";
  }

  if (/^ghi chu he thong:$/i.test(text)) {
    return "";
  }
  if (/lich trinh da duoc kiem tra tu dong/i.test(text)) {
    return "";
  }
  if (/^[-•]?\s*ly do phu hop:/i.test(text)) {
    return "";
  }

  text = text.replace(
    /\s*—\s*Nguon:\s*.*?(?=(?:\s*—\s*(?:Ly do chon|Map):)|(?:\.\s+Hanh dong:)|(?:\.\s+Link chặng:)|(?:\.\s*$)|$)/gi,
    "",
  );
  text = text.replace(
    /\s*—\s*Ly do chon:\s*.*?(?=(?:\s*—\s*Map:)|(?:\.\s+Hanh dong:)|(?:\.\s+Link chặng:)|(?:\.\s*$)|$)/gi,
    "",
  );
  text = text.replace(/\s*\((?:nguon map|nguồn map):\s*[^)]+\)/gi, "");
  text = text.replace(/\s*-\s*destination\b/gi, "");
  text = text.replace(/\(\s*,\s*/g, "(");
  text = text.replace(/,\s*,+/g, ", ");
  text = text.replace(/\(\s*\)/g, "");
  text = text.replace(" . ", ". ");
  text = text.replace(/\s{2,}/g, " ").trim();
  return text;
}

function splitActionClauses(text: string): string[] {
  return text
    .split(/\s*;\s*/g)
    .map((part) => part.trim())
    .filter(Boolean);
}

function expandDisplayLine(line: string): string[] {
  const cleanedLine = cleanDisplayLine(line);
  if (!cleanedLine) {
    return [""];
  }

  const linkMatch = cleanedLine.match(/\.\s+Link chặng:\s*(https?:\/\/\S+)/i);
  const linkUrl = linkMatch?.[1] || "";
  const withoutLink = cleanedLine.replace(/\.\s+Link chặng:\s*https?:\/\/\S+/i, "").trim();

  const actionMatch = withoutLink.match(/^(.*?)(?:\.\s+Hanh dong:\s*)(.*)$/i);
  if (!actionMatch) {
    return linkUrl ? [withoutLink, `- Link chặng: ${linkUrl}`] : [withoutLink];
  }

  const head = actionMatch[1]?.trim();
  const actions = splitActionClauses(actionMatch[2] || "");
  const expanded: string[] = [];

  if (head) {
    expanded.push(head.endsWith(".") ? head : `${head}.`);
  }

  actions.forEach((action, index) => {
    expanded.push(index === 0 ? `- Hanh dong: ${action}` : `- ${action}`);
  });

  if (linkUrl) {
    expanded.push(`- Link chặng: ${linkUrl}`);
  }

  return expanded.length > 0 ? expanded : [withoutLink];
}

function renderInlineLinks(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let matchIndex = 0;

  for (const match of text.matchAll(URL_PATTERN)) {
    const [url] = match;
    const start = match.index ?? 0;

    if (start > lastIndex) {
      nodes.push(text.slice(lastIndex, start));
    }

    nodes.push(
      <a
        key={`${url}-${start}`}
        className="message-link"
        href={url}
        target="_blank"
        rel="noreferrer"
        title={url}
      >
        {linkLabelForLine(url, text, matchIndex)}
      </a>,
    );

    lastIndex = start + url.length;
    matchIndex += 1;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes.length > 0 ? nodes : [text];
}

function renderMessageLine(line: string, index: number): ReactNode {
  const trimmed = line.trim();

  if (!trimmed) {
    return <div key={`empty-${index}`} className="message-spacer" aria-hidden="true" />;
  }

  let className = "message-line";
  let displayLine = line;
  if (/^KE HOACH DU LICH GOI Y/i.test(trimmed)) {
    className += " is-heading";
  } else if (/^NGAY\s+\d+/i.test(trimmed)) {
    className += " is-day";
  } else if (trimmed.endsWith(":") && !trimmed.startsWith("http")) {
    className += " is-section";
  } else if (/^•\s*(Sang|Trua|Chieu|Toi):/i.test(trimmed)) {
    className += " is-bullet level-1";
  } else if (/^-\s*Hanh dong:/i.test(trimmed)) {
    className += " is-bullet level-2 is-action";
  } else if (/^-\s*Link chặng:/i.test(trimmed)) {
    className += " is-bullet level-3 is-link-row";
  } else if (/^-\s/.test(trimmed)) {
    className += " is-bullet level-2";
  } else if (/^[•]/.test(trimmed)) {
    className += " is-bullet level-1";
  }

  if (className.includes("is-bullet")) {
    displayLine = trimmed.replace(/^[•-]\s*/, "");
  }

  return (
    <p key={`line-${index}`} className={className}>
      {renderInlineLinks(displayLine)}
    </p>
  );
}

function renderMessageContent(content: string): ReactNode {
  const displayLines = content.split(/\r?\n/).flatMap((line) => expandDisplayLine(line));
  return <div className="message-text">{displayLines.map((line, index) => renderMessageLine(line, index))}</div>;
}

function extractPlaceNames(line: string): string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  const pattern = /\btai\s+([^(\n.;]+?)(?=\s*(?:\(|—|\.|;|$))/gi;

  for (const match of line.matchAll(pattern)) {
    const name = match[1]?.replace(/\s+/g, " ").replace(/^[,\-\s]+|[,\-\s]+$/g, "").trim();
    if (!name) {
      continue;
    }
    const key = name.toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    names.push(name);
  }

  return names;
}

function summarizeSlotText(line: string, label: string): string {
  const names = extractPlaceNames(line);
  if (names.length >= 2 && label === "Sang") {
    return `${names[0]} -> ${names[1]}`;
  }
  if (names.length > 0) {
    return names.slice(0, 2).join(" -> ");
  }

  const fallback = line
    .replace(/^•\s*(Sang|Trua|Chieu|Toi):/i, "")
    .replace(/Hanh dong:\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();

  return fallback.length > 96 ? `${fallback.slice(0, 93)}...` : fallback;
}

function parseDaySlot(line: string): DaySlotSummary | null {
  const slots = [
    { prefix: "• Sang:", label: "Sang" },
    { prefix: "• Trua:", label: "Trua" },
    { prefix: "• Chieu:", label: "Chieu" },
    { prefix: "• Toi:", label: "Toi" },
  ];

  const matched = slots.find((slot) => line.startsWith(slot.prefix));
  if (!matched) {
    return null;
  }

  const text = summarizeSlotText(line, matched.label);
  if (!text) {
    return null;
  }

  return { label: matched.label, text };
}

function parsePlanDays(planText: string): DaySummary[] {
  const days: DaySummary[] = [];
  let currentDay: DaySummary | null = null;

  for (const rawLine of planText.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }

    const dayMatch = line.match(/^NGAY\s+(\d+)(?:\s*-\s*(.+))?/i);
    if (dayMatch) {
      if (currentDay) {
        days.push(currentDay);
      }
      currentDay = {
        title: `Ngay ${dayMatch[1]}`,
        theme: dayMatch[2]?.trim() || "",
        slots: [],
      };
      continue;
    }

    if (!currentDay) {
      continue;
    }

    const slot = parseDaySlot(line);
    if (slot) {
      currentDay.slots.push(slot);
      continue;
    }

  }

  if (currentDay) {
    days.push(currentDay);
  }

  return days;
}

function extractHotelInfo(metadata: AssistantMessageMetadata): {
  hotelName: string;
  hotelMapUrl: string | null;
} {
  const rawHotel = metadata.recommended_hotel;
  if (!rawHotel || typeof rawHotel !== "object") {
    return { hotelName: "", hotelMapUrl: null };
  }

  const hotelRecord = rawHotel as Record<string, unknown>;
  let hotel = hotelRecord;

  if (Array.isArray(hotelRecord.segments) && hotelRecord.segments.length > 0) {
    const firstSegment = hotelRecord.segments[0];
    if (firstSegment && typeof firstSegment === "object") {
      const segmentRecord = firstSegment as Record<string, unknown>;
      if (segmentRecord.hotel && typeof segmentRecord.hotel === "object") {
        hotel = segmentRecord.hotel as Record<string, unknown>;
      }
    }
  }

  return {
    hotelName: readString(hotel.name),
    hotelMapUrl:
      readString(hotel.map_place_uri) || readString(hotel.google_maps_uri) || readString(hotel.map_url) || null,
  };
}

function extractStayRecommendations(metadata: AssistantMessageMetadata): StayRecommendation[] {
  const raw = metadata.stay_recommendations;
  if (!Array.isArray(raw)) {
    return [];
  }

  return raw
    .map((item) => {
      if (!item || typeof item !== "object") {
        return null;
      }
      const record = item as Record<string, unknown>;
      const segment = readString(record.segment);
      const name = readString(record.name);
      if (!segment || !name) {
        return null;
      }
      return {
        segment,
        name,
        priceNote: readString(record.price_note),
        address: readString(record.address),
        whyFit: readString(record.why_fit),
        mapUrl: readString(record.map_url) || null,
      } satisfies StayRecommendation;
    })
    .filter((item): item is StayRecommendation => Boolean(item));
}

function extractDestinationFromAnswer(text: string): string {
  const match = text.match(/KE HOACH DU LICH GOI Y\s*-\s*(.+)/i);
  return match?.[1]?.trim() || "";
}

function formatDaysLabel(value: unknown, fallbackDays: number): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${value} ngay`;
  }

  const text = readString(value);
  if (text) {
    return /^\d+$/.test(text) ? `${text} ngay` : text;
  }

  return fallbackDays > 0 ? `${fallbackDays} ngay` : "";
}

function buildPlannerSnapshot(message: DraftMessage | null): PlannerSnapshot | null {
  if (!message) {
    return null;
  }

  const metadata =
    message.metadata && typeof message.metadata === "object"
      ? (message.metadata as AssistantMessageMetadata)
      : ({} as AssistantMessageMetadata);
  const collectedInfo =
    metadata.collected_info && typeof metadata.collected_info === "object"
      ? (metadata.collected_info as Record<string, unknown>)
      : {};
  const planText = readString(metadata.plan) || message.content;
  const daySummaries = parsePlanDays(planText);
  const hotelInfo = extractHotelInfo(metadata);
  const stayRecommendations = extractStayRecommendations(metadata);
  const followUp = Array.isArray(metadata.follow_up_questions)
    ? metadata.follow_up_questions.find((item) => typeof item === "string" && item.trim()) || null
    : null;

  return {
    destination: readString(collectedInfo.destination) || extractDestinationFromAnswer(message.content) || "Chuyen di hien tai",
    daysLabel: formatDaysLabel(collectedInfo.days, daySummaries.length),
    hotelName: hotelInfo.hotelName,
    hotelMapUrl: hotelInfo.hotelMapUrl,
    stayRecommendations,
    followUp,
    daySummaries,
    hasPlan: daySummaries.length > 0,
  };
}

function getLatestAssistantMessage(messages: DraftMessage[]): DraftMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && !message.pending) {
      return message;
    }
  }
  return null;
}

function SummaryPanel({
  snapshot,
  followUps,
  isPending,
}: {
  snapshot: PlannerSnapshot | null;
  followUps: string[];
  isPending: boolean;
}) {
  const nextPrompt = snapshot?.followUp || followUps[0] || null;

  if (!snapshot?.hasPlan) {
    return (
      <aside className="summary-panel">
        <div className="summary-panel-head">
          <span className="summary-kicker">Quick tab</span>
          <h3>Tom tat nhanh</h3>
          <p>Khung nay se rut gon thong tin chinh tung ngay de de theo doi va nho nhanh.</p>
        </div>

        <div className="summary-empty">
          <strong>{isPending ? "Dang tong hop lich trinh..." : "Chua co lich trinh de tom tat."}</strong>
          <p>
            {isPending
              ? "Khi planner xong, ben nay se hien thi ngay, dia diem chinh va link mo map ngan gon."
              : "Gui them yeu cau ve diem den, so ngay hoac ngan sach de minh dien vao day."}
          </p>
        </div>

        {nextPrompt ? (
          <div className="summary-note">
            <span>Can bo sung</span>
            <p>{nextPrompt}</p>
          </div>
        ) : null}
      </aside>
    );
  }

  return (
    <aside className="summary-panel">
      <div className="summary-panel-head">
        <span className="summary-kicker">Quick tab</span>
        <h3>Tom tat nhanh</h3>
        <p>
          {snapshot.destination}
          {snapshot.daysLabel ? ` · ${snapshot.daysLabel}` : ""}
        </p>
      </div>

      {snapshot.stayRecommendations.length > 0 ? (
        <div className="summary-card">
          <span className="summary-card-label">Luu tru</span>
          <div className="summary-stays">
            {snapshot.stayRecommendations.slice(0, 2).map((stay) => (
              <div key={`${stay.segment}-${stay.name}`} className="summary-stay-item">
                <strong>
                  {stay.segment}: {stay.name}
                </strong>
                {stay.priceNote ? <p>Gia: {stay.priceNote}</p> : null}
                {stay.address ? <p>Dia chi: {stay.address}</p> : null}
                {stay.whyFit ? <p>Phu hop: {stay.whyFit}</p> : null}
                {stay.mapUrl ? (
                  <a className="summary-link" href={stay.mapUrl} target="_blank" rel="noreferrer">
                    Mo map khach san
                  </a>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : snapshot.hotelName ? (
        <div className="summary-card">
          <span className="summary-card-label">Luu tru</span>
          <strong>{snapshot.hotelName}</strong>
          {snapshot.hotelMapUrl ? (
            <a className="summary-link" href={snapshot.hotelMapUrl} target="_blank" rel="noreferrer">
              Mo map khach san
            </a>
          ) : null}
        </div>
      ) : null}

      <div className="summary-days">
        {snapshot.daySummaries.map((day) => (
          <section key={`${day.title}-${day.theme}`} className="summary-day">
            <div className="summary-day-head">
              <strong>{day.title}</strong>
              {day.theme ? <span>{day.theme}</span> : null}
            </div>

            <div className="summary-slots">
              {day.slots.map((slot) => (
                <div key={`${day.title}-${slot.label}`} className="summary-slot">
                  <span className="summary-slot-label">{slot.label}</span>
                  <p>{slot.text}</p>
                </div>
              ))}
            </div>

          </section>
        ))}
      </div>

      {nextPrompt ? (
        <div className="summary-note">
          <span>Nhac tiep theo</span>
          <p>{nextPrompt}</p>
        </div>
      ) : null}
    </aside>
  );
}

export function ChatShell() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DraftMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [followUps, setFollowUps] = useState<string[]>([]);
  const [trace, setTrace] = useState<string[]>([]);
  const [debugSteps, setDebugSteps] = useState<DebugStep[]>([]);
  const [status, setStatus] = useState("Connecting to FastAPI...");
  const [error, setError] = useState<string | null>(null);
  const [pendingElapsedMs, setPendingElapsedMs] = useState(0);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    if (!isPending) {
      setPendingElapsedMs(0);
      return;
    }

    const startedAt = Date.now();
    setPendingElapsedMs(0);

    const timer = window.setInterval(() => {
      setPendingElapsedMs(Date.now() - startedAt);
    }, 180);

    return () => {
      window.clearInterval(timer);
    };
  }, [isPending]);

  useEffect(() => {
    let cancelled = false;

    startTransition(async () => {
      try {
        const session = await initSession();
        const items = await listConversations();
        if (cancelled) {
          return;
        }

        setPrincipal(session.principal);
        setConversations(items);
        setStatus("Anonymous session ready");

        if (items.length > 0) {
          const detail = await getConversation(items[0].id);
          if (cancelled) {
            return;
          }
          setActiveConversationId(detail.id);
          setMessages(toDraftMessages(detail));
          const signals = extractConversationSignals(detail);
          setFollowUps(signals.followUps);
          setTrace(signals.trace);
          setDebugSteps(signals.debugSteps);
        }
      } catch (loadError) {
        if (cancelled) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Unable to reach the backend.");
        setStatus("FastAPI connection failed");
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshConversations(targetConversationId?: string) {
    const items = await listConversations();
    setConversations(items);

    const nextConversationId = targetConversationId || activeConversationId;
    if (!nextConversationId) {
      return;
    }

    const detail = await getConversation(nextConversationId);
    setActiveConversationId(detail.id);
    setMessages(toDraftMessages(detail));
    const signals = extractConversationSignals(detail);
    setFollowUps(signals.followUps);
    setTrace(signals.trace);
    setDebugSteps(signals.debugSteps);
  }

  async function handleConversationSelect(conversationId: string) {
    setError(null);
    setStatus("Loading conversation...");

    try {
      const detail = await getConversation(conversationId);
      setActiveConversationId(detail.id);
      setMessages(toDraftMessages(detail));
      const signals = extractConversationSignals(detail);
      setFollowUps(signals.followUps);
      setTrace(signals.trace);
      setDebugSteps(signals.debugSteps);
      setStatus("Conversation loaded");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Conversation load failed.");
      setStatus("Conversation load failed");
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || isPending) {
      return;
    }

    const optimisticId = `draft-${Date.now()}`;
    setError(null);
    setStatus("FastAPI is planning your trip...");
    setDraft("");
    setFollowUps([]);
    setTrace([]);
    setDebugSteps([]);
    setMessages((current) => [
      ...current,
      { id: optimisticId, role: "user", content },
      { id: `${optimisticId}-assistant`, role: "assistant", content: "Thinking...", pending: true },
    ]);

    startTransition(async () => {
      try {
        const response = await sendChat(content, activeConversationId || undefined);
        const conversationId = response.conversation_id || activeConversationId || null;

        await refreshConversations(conversationId || undefined);
        setFollowUps(response.follow_up_questions || []);
        setTrace(normalizeTrace(response.trace));
        setDebugSteps(response.debug_steps || []);
        setStatus(response.conversation_stage === "intake" ? "Need a little more info" : "Plan ready");
      } catch (submitError) {
        setMessages((current) =>
          current.filter((message) => message.id !== optimisticId && message.id !== `${optimisticId}-assistant`),
        );
        setError(submitError instanceof Error ? submitError.message : "Chat request failed.");
        setTrace([]);
        setDebugSteps([]);
        setStatus("Chat request failed");
      }
    });
  }

  const canShowEmptyState = !isPending && messages.length === 0;
  const latestAssistantMessage = getLatestAssistantMessage(messages);
  const plannerSnapshot = buildPlannerSnapshot(latestAssistantMessage);
  const pendingSteps = buildPendingDebugSteps(pendingElapsedMs);
  const traceSteps = isPending ? buildTraceSteps(pendingSteps) : buildTraceSteps(debugSteps);

  return (
    <main className="shell">
      <section className="hero">
        <div className="hero-copy">
          <span className="eyebrow">Next.js + FastAPI</span>
          <h1>Travel planning UI on top of your FastAPI orchestration layer.</h1>
          <p>
            The frontend owns the experience. FastAPI keeps the planner, session cookie, conversations,
            and plans.
          </p>
        </div>

        <div className="hero-card">
          <div className="hero-card-label">Current principal</div>
          <div className="hero-card-value">{principal ? principal.type : "booting"}</div>
          <div className="hero-card-meta">{principal ? principal.id.slice(0, 12) : "waiting for session"}</div>
          <div className="status-pill">{status}</div>
        </div>
      </section>

      <section className="workspace">
        <aside className="sidebar">
          <div className="sidebar-header">
            <h2>Conversations</h2>
            <span>{conversations.length}</span>
          </div>

          <button
            className="ghost-button"
            type="button"
            onClick={() => {
              setActiveConversationId(null);
              setMessages([]);
              setFollowUps([]);
              setTrace([]);
              setDebugSteps([]);
              setStatus("Fresh conversation");
            }}
          >
            Start new chat
          </button>

          <div className="conversation-list">
            {conversations.length === 0 ? (
              <div className="empty-card">No saved conversations yet.</div>
            ) : (
              conversations.map((conversation) => (
                <button
                  key={conversation.id}
                  type="button"
                  className={`conversation-item${conversation.id === activeConversationId ? " is-active" : ""}`}
                  onClick={() => handleConversationSelect(conversation.id)}
                >
                  <span className="conversation-title">{conversation.title}</span>
                  <span className="conversation-meta">{formatRelativeLabel(conversation.updated_at)}</span>
                  <span className="conversation-preview">
                    {conversation.latest_message_preview || "No preview yet"}
                  </span>
                </button>
              ))
            )}
          </div>
        </aside>

        <section className="chat-panel">
          <div className="chat-header">
            <div>
              <h2>Planner Console</h2>
              <p>Ask for an itinerary, then reuse the same conversation through the FastAPI session.</p>
            </div>
          </div>

          <div className="chat-body">
            <div className="chat-main">
              <div className="message-list">
                {canShowEmptyState ? (
                  <div className="empty-chat">
                    <h3>Ready for the first request</h3>
                    <p>Example: build a 3-day Da Nang itinerary with beach views and lower transport cost.</p>
                  </div>
                ) : (
                  messages.map((message) => (
                    <article
                      key={message.id}
                      className={`message-bubble${message.role === "assistant" ? " assistant" : " user"}${
                        message.pending ? " pending" : ""
                      }`}
                    >
                      <span className="message-role">{message.role === "assistant" ? "Assistant" : "You"}</span>
                      {renderMessageContent(message.content)}
                    </article>
                  ))
                )}
              </div>

              <div className="trace-panel">
                <div className="trace-header">
                  <span>Thinking flow</span>
                  <span>
                    {isPending
                      ? `Running · ${formatElapsedMs(pendingElapsedMs)}`
                      : trace.length > 0
                        ? "Completed"
                        : "Idle"}
                  </span>
                </div>
                <div className="trace-list">
                  {traceSteps.map((step, index) => (
                    <span key={`${step.label}-${index}`} className={`trace-chip status-${step.status}`}>
                      {index + 1}. {step.label}
                    </span>
                  ))}
                  {!isPending && trace.length === 0 ? <span className="trace-empty">No orchestration trace yet.</span> : null}
                </div>
              </div>

              <div className="debug-panel">
                <div className="trace-header">
                  <span>Step-by-step debug</span>
                  <span>
                    {isPending
                      ? `Tracking · ${formatElapsedMs(pendingElapsedMs)}`
                      : debugSteps.length > 0
                        ? `${debugSteps.length} steps`
                        : "Idle"}
                  </span>
                </div>
                <div className="debug-steps">
                  {(isPending ? pendingSteps : debugSteps).map((step) => (
                    <article key={step.key} className="debug-step">
                      <div className="debug-step-head">
                        <strong>{step.title}</strong>
                        <span className={`debug-status status-${step.status}`}>{statusLabel(step.status)}</span>
                      </div>
                      <p className="debug-summary">{step.summary}</p>
                      {Object.keys(step.details || {}).length > 0 ? (
                        <div className="debug-details">
                          {Object.entries(step.details || {}).map(([key, value]) => (
                            <div key={`${step.key}-${key}`} className="debug-detail-row">
                              <span className="debug-detail-key">{key}</span>
                              <pre className="debug-detail-value">{formatDebugValue(value)}</pre>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </article>
                  ))}
                  {!isPending && debugSteps.length === 0 ? (
                    <span className="trace-empty">No debug steps available yet.</span>
                  ) : null}
                </div>
              </div>

              {followUps.length > 0 ? (
                <div className="follow-ups">
                  <span>Follow-up prompts</span>
                  <ul>
                    {followUps.map((question) => (
                      <li key={question}>{question}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>

            <SummaryPanel snapshot={plannerSnapshot} followUps={followUps} isPending={isPending} />
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <label className="composer-label" htmlFor="message">
              Message
            </label>
            <textarea
              id="message"
              className="composer-input"
              rows={4}
              placeholder="Describe the trip you want the planner to build..."
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <div className="composer-actions">
              {error ? <span className="error-text">{error}</span> : <span className="hint-text">Session is cookie-backed.</span>}
              <button className="submit-button" type="submit" disabled={isPending || !draft.trim()}>
                {isPending ? "Planning..." : "Send to FastAPI"}
              </button>
            </div>
          </form>
        </section>
      </section>
    </main>
  );
}
