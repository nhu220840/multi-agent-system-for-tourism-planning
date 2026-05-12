"use client";

import { FormEvent, KeyboardEvent, MouseEvent, ReactNode, useEffect, useRef, useState } from "react";

import {
  type ChatResponse,
  type ConversationDetail,
  type ConversationSummary,
  type DebugStep,
  type Principal,
  deleteAllConversations,
  deleteConversation,
  getConversation,
  initSession,
  listConversations,
  sendChatStream,
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
  missing_fields?: string[] | null;
  recommended_hotel?: Record<string, unknown> | null;
  route_plan?: Record<string, unknown>[] | null;
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

type PlannedStay = {
  segment: string;
  name: string;
  address: string;
  mapUrl: string | null;
};

type RouteLeg = {
  dayNumber: number | null;
  sequence: number;
  legLabel: string;
  from: string;
  to: string;
  fromAddress: string;
  toAddress: string;
  distanceKm: string;
  etaMin: string;
  modeLabel: string;
  modeTone: "walk" | "ride" | "car" | "default";
  modeBadge: string;
  directionUrl: string | null;
};

type PlannerSnapshot = {
  destination: string;
  daysLabel: string;
  plannedStays: PlannedStay[];
  stayRecommendations: StayRecommendation[];
  routeLegs: RouteLeg[];
  followUp: string | null;
  daySummaries: DaySummary[];
  hasPlan: boolean;
};

type ConversationListItem = {
  key: string;
  conversationId: string | null;
  title: string;
  created_at: string;
  updated_at: string;
  latest_message_preview?: string | null;
  message_count: number;
};

type ConversationViewState = {
  conversationId: string | null;
  messages: DraftMessage[];
  followUps: string[];
  trace: string[];
  debugSteps: DebugStep[];
  conversationStage: string | null;
  pendingMode: "intake" | "planning" | null;
  pendingStartedAt: number | null;
};

const URL_PATTERN = /https?:\/\/[^\s]+/g;
const STARTER_ASSISTANT_MESSAGE =
  "Chào bạn, mình là chatbot tư vấn lập kế hoạch du lịch Đà Nẵng. " +
  "Mình sẽ hỏi bạn từng câu ngắn để gom đủ thông tin, sau đó tổng hợp lại và lên lịch trình phù hợp.\n\n" +
  "Trước tiên: Bạn dự định đi mấy ngày?";

function createConversationViewState(conversationId: string | null = null): ConversationViewState {
  return {
    conversationId,
    messages: [],
    followUps: [],
    trace: [],
    debugSteps: [],
    conversationStage: null,
    pendingMode: null,
    pendingStartedAt: null,
  };
}

function toConversationListItem(summary: ConversationSummary): ConversationListItem {
  return {
    key: summary.id,
    conversationId: summary.id,
    title: summary.title,
    created_at: summary.created_at,
    updated_at: summary.updated_at,
    latest_message_preview: summary.latest_message_preview,
    message_count: summary.message_count,
  };
}

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
  conversationStage: string | null;
} {
  if (!conversation) {
    return { followUps: [], trace: [], debugSteps: [], conversationStage: null };
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
    const conversationStage =
      typeof (message.metadata as { conversation_stage?: string }).conversation_stage === "string"
        ? String((message.metadata as { conversation_stage?: string }).conversation_stage)
        : null;
    return { followUps, trace, debugSteps, conversationStage };
  }

  return { followUps: [], trace: [], debugSteps: [], conversationStage: null };
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

  return PENDING_STEP_BLUEPRINTS.map((step) => {
    const startMs = offsetMs;
    const endMs = offsetMs + step.durationMs;
    offsetMs = endMs;

    let status: DebugStep["status"] = "queued";
    let summary = step.queuedSummary;
    let elapsedForStep = 0;

    if (elapsedMs >= endMs) {
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

function isIntakeStage(stage: string | null | undefined): boolean {
  return (stage || "").trim().toLowerCase() === "intake";
}

function latestAssistantMetadata(messages: DraftMessage[]): AssistantMessageMetadata | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== "assistant" || !message.metadata || typeof message.metadata !== "object") {
      continue;
    }
    return message.metadata as AssistantMessageMetadata;
  }
  return null;
}

function inferPendingMode(
  state: ConversationViewState,
  currentAnswer: string,
): "intake" | "planning" {
  if (!isIntakeStage(state.conversationStage)) {
    return "planning";
  }

  const metadata = latestAssistantMetadata(state.messages);
  const missingFields = Array.isArray(metadata?.missing_fields)
    ? metadata?.missing_fields?.filter((item) => typeof item === "string" && String(item).trim())
    : [];

  if (missingFields.length <= 1 && currentAnswer.trim()) {
    return "planning";
  }

  return "intake";
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

function toFriendlySubmitError(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    if (/timed out/i.test(error.message)) {
      return "Backend phản hồi quá lâu (timeout). Vui lòng thử lại hoặc rút gọn yêu cầu để hệ thống xử lý nhanh hơn.";
    }
    return error.message;
  }
  return "Không thể kết nối tới backend. Vui lòng thử lại.";
}

function statusForStreamStage(stage: string, status: string, needsReplan = false): string {
  if (stage === "intake") {
    return status === "completed" ? "Đã kiểm tra xong thông tin đầu vào" : "Đang kiểm tra thông tin đầu vào...";
  }
  if (stage === "planning") {
    return needsReplan ? "Đang điều chỉnh lại lịch trình..." : "Đang lên lịch trình...";
  }
  if (stage === "validator") {
    return needsReplan
      ? "Validator yêu cầu lập lại kế hoạch..."
      : status === "completed"
        ? "Đã rà soát xong lịch trình"
        : "Đang rà soát lịch trình...";
  }
  if (stage === "response") {
    return status === "completed" ? "Đã soạn xong phản hồi" : "Đang soạn phản hồi...";
  }
  return "Đang xử lý yêu cầu...";
}

function truncateMiddle(value: string, maxLength = 48): string {
  if (value.length <= maxLength) {
    return value;
  }
  const sideLength = Math.max(10, Math.floor((maxLength - 3) / 2));
  return `${value.slice(0, sideLength)}...${value.slice(-sideLength)}`;
}

function appendAssistantDelta(messages: DraftMessage[], messageId: string, delta: string): DraftMessage[] {
  let found = false;
  const updated = messages.map((message) => {
    if (message.id !== messageId) {
      return message;
    }
    found = true;
    return {
      ...message,
      content: `${message.content}${delta}`,
    };
  });

  if (found) {
    return updated;
  }

  return [...messages, { id: messageId, role: "assistant", content: delta, pending: true }];
}

function finalizeAssistantMessage(messages: DraftMessage[], messageId: string, response: ChatResponse): DraftMessage[] {
  let found = false;
  const metadata = response as unknown as Record<string, unknown>;
  const updated = messages.map((message) => {
    if (message.id !== messageId) {
      return message;
    }
    found = true;
    return {
      ...message,
      content: response.answer,
      metadata,
      pending: false,
    };
  });

  if (found) {
    return updated;
  }

  return [
    ...messages,
    {
      id: messageId,
      role: "assistant",
      content: response.answer,
      metadata,
      pending: false,
    },
  ];
}

function replaceAssistantContent(messages: DraftMessage[], messageId: string, content: string): DraftMessage[] {
  let found = false;
  const updated = messages.map((message) => {
    if (message.id !== messageId) {
      return message;
    }
    found = true;
    return {
      ...message,
      content,
    };
  });

  if (found) {
    return updated;
  }

  return [...messages, { id: messageId, role: "assistant", content, pending: true }];
}

function progressCopyForStage(stage: string, status: string, needsReplan = false): string | null {
  if (status !== "started") {
    return null;
  }
  if (stage === "intake") {
    return "Mình đang đọc yêu cầu và kiểm tra xem đã đủ thông tin để lập kế hoạch chưa...\n";
  }
  if (stage === "planning") {
    return needsReplan
      ? "Mình đang dựng lại lịch trình để khớp hơn với các ràng buộc vừa kiểm tra...\n"
      : "Mình đang tìm điểm phù hợp, ghép tuyến đường và sắp lịch trình theo từng ngày...\n";
  }
  if (stage === "validator") {
    return "Mình đang rà lại logic lịch trình để tránh bị lệch tuyến hoặc thiếu chặng quan trọng...\n";
  }
  if (stage === "response") {
    return "Mình đang viết lại câu trả lời theo dạng dễ đọc để bạn dùng ngay...\n";
  }
  return null;
}

function linkLabelForLine(url: string, line: string, index: number): string {
  const lower = line.toLowerCase();
  if (url.includes("/routes/") && url.includes("maps.track-asia.com")) {
    return "Chỉ đường";
  }
  if (lower.includes("ban do tuyen ngay") || lower.includes("bản đồ tuyến ngày")) {
    return index > 0 ? `Mở tuyến ${index + 1}` : "Mở tuyến";
  }
  if (lower.includes("map tung chang") || lower.includes("bản đồ từng chặng")) {
    return `Bản đồ chặng ${index + 1}`;
  }
  if (lower.includes(" map:")) {
    return index > 0 ? `Mở map ${index + 1}` : "Mở map";
  }
  if (url.includes("/place/") && url.includes("maps.track-asia.com")) {
    return "Xem map";
  }
  if (lower.includes("directions")) {
    return "Chỉ đường";
  }

  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    if (host.includes("track-asia.com")) {
      return url.includes("/routes/") ? "Chỉ đường" : "Xem map";
    }
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
  if (/^[─—-]{5,}$/.test(text)) {
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
  if (/^(tom tat|thong tin) di chuyen:\s*$/i.test(text)) {
    return "";
  }
  if (/^[-•]?\s*chang\s+\d+\s*:/i.test(text)) {
    return "";
  }
  if (/^[-•]?\s*Link chặng:/i.test(text)) {
    return "";
  }
  if (/^[-•]?\s*Nghi dem\s*:/i.test(text)) {
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
  return normalizeVietnameseDisplay(text);
}

function normalizeVietnameseDisplay(text: string): string {
  let out = text;
  const replacements: Array<[RegExp, string]> = [
    [/\bKE HOACH DU LICH GOI Y\b/gi, "KẾ HOẠCH DU LỊCH GỢI Ý"],
    [/\bTOM TAT NHANH\b/gi, "TÓM TẮT NHANH"],
    [/\bTHOI TIET\s*&\s*THOI DIEM\b/gi, "THỜI TIẾT & THỜI ĐIỂM"],
    [/\bDIEM NHAN HANH TRINH\b/gi, "ĐIỂM NHẤN HÀNH TRÌNH"],
    [/\bNOI LUU TRU DE XUAT\b/gi, "NƠI LƯU TRÚ ĐỀ XUẤT"],
    [/\bLICH TRINH CHI TIET\b/gi, "LỊCH TRÌNH CHI TIẾT"],
    [/\bDI CHUYEN GOI Y\b/gi, "DI CHUYỂN GỢI Ý"],
    [/\bMEO\s*&\s*LUU Y\b/gi, "MẸO & LƯU Ý"],
    [/\bBUOC TIEP THEO\b/gi, "BƯỚC TIẾP THEO"],
    [/\bDa Nang\b/gi, "Đà Nẵng"],
    [/\bHoi An\b/gi, "Hội An"],
    [/\bQuang Nam\b/gi, "Quảng Nam"],
    [/\bHanh trinh:/gi, "Hành trình:"],
    [/\bNgay\b/gi, "Ngày"],
    [/\bChang di\b/gi, "Chặng đi"],
    [/\bChi duong\b/gi, "Chỉ đường"],
    [/\bHanh dong\b/gi, "Hoạt động"],
    [/\bSang:/gi, "Sáng:"],
    [/\bTrua:/gi, "Trưa:"],
    [/\bChieu:/gi, "Chiều:"],
    [/\bToi:/gi, "Tối:"],
    [/\bdiem den\b/gi, "điểm đến"],
    [/\bhanh trinh\b/gi, "hành trình"],
    [/\btrai nghiem\b/gi, "trải nghiệm"],
    [/\btham quan\b/gi, "tham quan"],
    [/\bam thuc\b/gi, "ẩm thực"],
    [/\bvan hoa\b/gi, "văn hoá"],
    [/\btham khao\b/gi, "tham khảo"],
    [/\bthoi tiet\b/gi, "thời tiết"],
    [/\bcu the\b/gi, "cụ thể"],
    [/\bdoi chieu\b/gi, "đối chiếu"],
    [/\bcan mang theo gi\b/gi, "cần mang theo gì"],
    [/\bde o khung\b/gi, "để ở khung"],
  ];
  replacements.forEach(([pattern, replacement]) => {
    out = out.replace(pattern, replacement);
  });
  return out;
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

  if (/^[-•]?\s*Link chặng:\s*https?:\/\/\S+$/i.test(cleanedLine)) {
    return [""];
  }
  if (cleanedLine.includes("->") && /https?:\/\//i.test(cleanedLine) && !/\b(?:km|phut)\b/i.test(cleanedLine)) {
    return [""];
  }

  const linkMatch = cleanedLine.match(/\.\s+Link chặng:\s*(https?:\/\/\S+)/i);
  const linkUrl = linkMatch?.[1] || "";
  const withoutLink = cleanedLine.replace(/\.\s+Link chặng:\s*https?:\/\/\S+/i, "").trim();

  const actionMatch = withoutLink.match(/^(.*?)(?:\.\s+(?:Hanh dong|Hoạt động):\s*)(.*)$/i);
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
    expanded.push(index === 0 ? `- Hoạt động: ${action}` : `- ${action}`);
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
  const normalizedLine = normalizeVietnameseDisplay(line);
  const trimmed = normalizedLine.trim();

  if (!trimmed) {
    return <div key={`empty-${index}`} className="message-spacer" aria-hidden="true" />;
  }

  let className = "message-line";
  let displayLine = normalizedLine;
  if (/^(KẾ HOẠCH DU LỊCH GỢI Ý|KE HOACH DU LICH GOI Y)/i.test(trimmed)) {
    className += " is-heading";
  } else if (/^(?:▸\s*)?Ngày\s+\d+/i.test(trimmed) || /^(?:▸\s*)?NGAY\s+\d+/i.test(trimmed) || /^▸\s*Ngày/i.test(trimmed)) {
    className += " is-day";
  } else if (trimmed.endsWith(":") && !trimmed.startsWith("http")) {
    className += " is-section";
  } else if (/^•\s*(Sáng|Trưa|Chiều|Tối|Sang|Trua|Chieu|Toi):/i.test(trimmed)) {
    className += " is-bullet level-1";
  } else if (/^-\s*(Hoạt động|Hanh dong):/i.test(trimmed)) {
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
  const pattern = /\btại\s+([^(\n.;]+?)(?=\s*(?:\(|—|\.|;|$))|\btai\s+([^(\n.;]+?)(?=\s*(?:\(|—|\.|;|$))/gi;

  for (const match of line.matchAll(pattern)) {
    const name = (match[1] ?? match[2])?.replace(/\s+/g, " ").replace(/^[,\-\s]+|[,\-\s]+$/g, "").trim();
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
  if (names.length >= 2 && label === "Sáng") {
    return `${names[0]} -> ${names[1]}`;
  }
  if (names.length > 0) {
    return names.slice(0, 2).join(" -> ");
  }

  const fallback = line
    .replace(/^•\s*(Sáng|Trưa|Chiều|Tối|Sang|Trua|Chieu|Toi):/i, "")
    .replace(/(?:Hoạt động|Hanh dong):\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();

  const normalized = normalizeVietnameseDisplay(fallback);
  return normalized.length > 96 ? `${normalized.slice(0, 93)}...` : normalized;
}

function parseDaySlot(line: string): DaySlotSummary | null {
  const slots = [
    { prefix: "• Sáng:", label: "Sáng" },
    { prefix: "• Sang:", label: "Sáng" },
    { prefix: "• Trưa:", label: "Trưa" },
    { prefix: "• Trua:", label: "Trưa" },
    { prefix: "• Chiều:", label: "Chiều" },
    { prefix: "• Chieu:", label: "Chiều" },
    { prefix: "• Tối:", label: "Tối" },
    { prefix: "• Toi:", label: "Tối" },
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

    const dayMatch = line.match(/^(?:▸\s*)?(?:NGAY|Ngày|NGÀY)\s+(\d+)(?:\s*[-—]\s*(.+))?/i);
    if (dayMatch) {
      if (currentDay) {
        days.push(currentDay);
      }
      currentDay = {
        title: `Ngày ${dayMatch[1]}`,
        theme: normalizeVietnameseDisplay(dayMatch[2]?.trim() || ""),
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

function extractPlannedStays(metadata: AssistantMessageMetadata): PlannedStay[] {
  const rawHotel = metadata.recommended_hotel;
  if (!rawHotel || typeof rawHotel !== "object") {
    return [];
  }

  const hotelRecord = rawHotel as Record<string, unknown>;
  if (Array.isArray(hotelRecord.segments) && hotelRecord.segments.length > 0) {
    return hotelRecord.segments
      .map((segment) => {
        if (!segment || typeof segment !== "object") {
          return null;
        }
        const segmentRecord = segment as Record<string, unknown>;
        const hotel =
          segmentRecord.hotel && typeof segmentRecord.hotel === "object"
            ? (segmentRecord.hotel as Record<string, unknown>)
            : null;
        const name = readString(hotel?.name);
        if (!name) {
          return null;
        }
        return {
          segment:
            readString(segmentRecord.days_label) ||
            readString(segmentRecord.city_label) ||
            readString(segmentRecord.city_key) ||
            "Lịch trình",
          name,
          address: readString(hotel?.address),
          mapUrl: readString(hotel?.map_place_uri) || readString(hotel?.google_maps_uri) || readString(hotel?.map_url) || null,
        } satisfies PlannedStay;
      })
      .filter((item): item is PlannedStay => Boolean(item));
  }

  const hotelName = readString(hotelRecord.name);
  if (!hotelName) {
    return [];
  }

  return [
    {
      segment: "Lịch trình",
      name: hotelName,
      address: readString(hotelRecord.address),
      mapUrl:
        readString(hotelRecord.map_place_uri) ||
        readString(hotelRecord.google_maps_uri) ||
        readString(hotelRecord.map_url) ||
        null,
    },
  ];
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

function dedupeAlternateStayRecommendations(
  plannedStays: PlannedStay[],
  stayRecommendations: StayRecommendation[],
): StayRecommendation[] {
  const plannedNames = new Set(
    plannedStays
      .map((stay) => stay.name.trim().toLowerCase())
      .filter(Boolean),
  );

  return stayRecommendations.filter((stay) => !plannedNames.has(stay.name.trim().toLowerCase()));
}

function formatDistanceLabel(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${value.toFixed(value >= 10 ? 0 : 1)} km`;
  }
  const text = readString(value);
  if (!text) {
    return "";
  }
  return text.toLowerCase().includes("km") ? text : `${text} km`;
}

function formatEtaLabel(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${Math.round(value)} phut`;
  }
  const text = readString(value);
  if (!text) {
    return "";
  }
  return /\bphut\b/i.test(text) ? text : `${text} phut`;
}

function parseDistanceKm(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  const text = readString(value);
  if (!text) {
    return null;
  }
  const match = text.match(/-?\d+(?:\.\d+)?/);
  if (!match) {
    return null;
  }
  const parsed = Number(match[0]);
  return Number.isFinite(parsed) ? parsed : null;
}

function extractRouteLegs(metadata: AssistantMessageMetadata): RouteLeg[] {
  const raw = metadata.route_plan;
  if (!Array.isArray(raw)) {
    return [];
  }

  return raw
    .map((item) => {
      if (!item || typeof item !== "object") {
        return null;
      }
      const record = item as Record<string, unknown>;
      const from = readString(record.from);
      const to = readString(record.to);
      if (!from || !to) {
        return null;
      }
      const rawDay = typeof record.day === "number" ? record.day : Number(readString(record.day) || NaN);
      const dayNumber = Number.isFinite(rawDay) ? rawDay : null;
      const modeLabel = readString(record.mode_label) || readString(record.recommended_mode);
      const distanceKmValue = parseDistanceKm(record.distance_km);
      const modeMeta = classifyRouteMode(modeLabel, distanceKmValue);
      return {
        dayNumber,
        sequence:
          typeof record.sequence === "number" && Number.isFinite(record.sequence) ? record.sequence : Number.MAX_SAFE_INTEGER,
        legLabel: readString(record.leg_label) || "",
        from,
        to,
        fromAddress: readString(record.from_address),
        toAddress: readString(record.to_address),
        distanceKm: formatDistanceLabel(record.distance_km),
        etaMin: formatEtaLabel(record.eta_min),
        modeLabel,
        modeTone: modeMeta.tone,
        modeBadge: modeMeta.badge,
        directionUrl: readString(record.segment_map_url) || null,
      } satisfies RouteLeg;
    })
    .filter((item): item is RouteLeg => Boolean(item))
    .sort((a, b) => {
      const dayA = a.dayNumber ?? Number.MAX_SAFE_INTEGER;
      const dayB = b.dayNumber ?? Number.MAX_SAFE_INTEGER;
      return dayA - dayB || a.sequence - b.sequence;
    });
}

function classifyRouteMode(modeLabel: string, distanceKm: number | null): { tone: RouteLeg["modeTone"]; badge: string } {
  if (distanceKm != null) {
    if (distanceKm < 1) {
      return { tone: "walk", badge: "Di bo" };
    }
    if (distanceKm < 4) {
      return { tone: "ride", badge: "Xe may/Grab" };
    }
    return { tone: "car", badge: "Grab/oto" };
  }

  const lower = modeLabel.toLowerCase();
  if (!lower) {
    return { tone: "default", badge: "Route" };
  }
  if (lower.includes("di bo") || lower.includes("walking") || lower.includes("walk")) {
    return { tone: "walk", badge: "Di bo" };
  }
  if (lower.includes("xe may") || lower.includes("motor") || lower.includes("moto") || lower.includes("scooter")) {
    return { tone: "ride", badge: "Xe may" };
  }
  if (lower.includes("grab") || lower.includes("oto") || lower.includes("ô tô") || lower.includes("car")) {
    return { tone: "car", badge: "Grab/oto" };
  }
  return { tone: "default", badge: modeLabel };
}

function extractDestinationFromAnswer(text: string): string {
  const match = text.match(/(?:KẾ HOẠCH DU LỊCH GỢI Ý|KE HOACH DU LICH GOI Y)\s*[—-]\s*(.+)/i);
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
  const plannedStays = extractPlannedStays(metadata);
  const stayRecommendations = dedupeAlternateStayRecommendations(plannedStays, extractStayRecommendations(metadata));
  const routeLegs = extractRouteLegs(metadata);
  const followUp = Array.isArray(metadata.follow_up_questions)
    ? metadata.follow_up_questions.find((item) => typeof item === "string" && item.trim()) || null
    : null;

  return {
    destination: normalizeVietnameseDisplay(
      readString(collectedInfo.destination) || extractDestinationFromAnswer(message.content) || "Chuyến đi hiện tại",
    ),
    daysLabel: formatDaysLabel(collectedInfo.days, daySummaries.length),
    plannedStays,
    stayRecommendations,
    routeLegs,
    followUp,
    daySummaries,
    hasPlan: daySummaries.length > 0,
  };
}

function dayNumberFromTitle(title: string): number | null {
  const match = title.match(/\b(?:Ngày|Ngay)\s+(\d+)\b/i);
  if (!match) {
    return null;
  }
  const parsed = Number(match[1]);
  return Number.isFinite(parsed) ? parsed : null;
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
  conversationStage,
  pendingMode,
}: {
  snapshot: PlannerSnapshot | null;
  followUps: string[];
  isPending: boolean;
  conversationStage: string | null;
  pendingMode: "intake" | "planning" | null;
}) {
  const nextPrompt = snapshot?.followUp || followUps[0] || null;
  const awaitingMoreInfo = isIntakeStage(conversationStage);
  const pendingPlanning = isPending && pendingMode === "planning";

  if (!snapshot?.hasPlan) {
    return (
      <aside className="summary-panel">
        <div className="summary-panel-head">
          <span className="summary-kicker">Quick tab</span>
          <h3>Tóm tắt nhanh</h3>
          <p>
            {awaitingMoreInfo
              ? "Mình đang ở bước intake: hỏi từng câu để lấy đủ thông tin trước khi bắt đầu planning."
              : "Khung này sẽ rút gọn thông tin chính từng ngày để dễ theo dõi và nhớ nhanh."}
          </p>
        </div>

        <div className="summary-empty">
          <strong>
            {pendingPlanning
              ? "Đang tổng hợp lịch trình..."
              : isPending
                ? "Đang kiểm tra câu trả lời mới."
              : awaitingMoreInfo
                ? "Đang thu thập thêm thông tin."
                : "Chưa có lịch trình để tóm tắt."}
          </strong>
          <p>
            {pendingPlanning
              ? "Khi planner xong, bên này sẽ hiển thị ngày, địa điểm chính và link mở map ngắn gọn."
              : isPending
                ? "Nếu thông tin bạn vừa gửi đã đủ, hệ thống sẽ chuyển sang planning ngay sau bước kiểm tra này."
              : awaitingMoreInfo
                ? "Chatbot sẽ tiếp tục hỏi từng câu. Khi đủ dữ liệu thì mới chuyển sang thinking/planning."
                : "Gửi thêm yêu cầu về điểm đến, số ngày hoặc ngân sách để mình điền vào đây."}
          </p>
        </div>

        {nextPrompt ? (
          <div className="summary-note">
            <span>Cần bổ sung</span>
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
        <h3>Tóm tắt nhanh</h3>
        <p>
          {snapshot.destination}
          {snapshot.daysLabel ? ` · ${snapshot.daysLabel}` : ""}
        </p>
      </div>

      {snapshot.plannedStays.length > 0 ? (
        <div className="summary-card">
          <span className="summary-card-label">Lưu trú trong lịch trình</span>
          <div className="summary-stays">
            {snapshot.plannedStays.map((stay) => (
              <div key={`${stay.segment}-${stay.name}`} className="summary-stay-item">
                <strong>
                  {stay.segment}: {stay.name}
                </strong>
                {stay.address ? <p>Địa chỉ: {stay.address}</p> : null}
                {stay.mapUrl ? (
                  <a className="summary-link" href={stay.mapUrl} target="_blank" rel="noreferrer">
                    Mở map khách sạn
                  </a>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {snapshot.stayRecommendations.length > 0 ? (
        <div className="summary-card">
          <span className="summary-card-label">Khách sạn tham khảo thêm</span>
          <div className="summary-stays">
            {snapshot.stayRecommendations.slice(0, 2).map((stay) => (
              <div key={`${stay.segment}-${stay.name}`} className="summary-stay-item">
                <strong>
                  {stay.segment}: {stay.name}
                </strong>
                {stay.priceNote ? <p>Giá: {stay.priceNote}</p> : null}
                {stay.address ? <p>Địa chỉ: {stay.address}</p> : null}
                {stay.whyFit ? <p>Phù hợp: {stay.whyFit}</p> : null}
                {stay.mapUrl ? (
                  <a className="summary-link" href={stay.mapUrl} target="_blank" rel="noreferrer">
                    Mở map khách sạn
                  </a>
                ) : null}
              </div>
            ))}
          </div>
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

      {snapshot.routeLegs.length > 0 ? (
        <section className="summary-route-block">
          <span className="summary-card-label">Chỉ dẫn từng chặng</span>
          <div className="route-leg-list">
            {snapshot.routeLegs.map((leg, index) => (
              <article
                key={`${leg.dayNumber ?? "day"}-${leg.sequence}-${leg.from}-${leg.to}-${index}`}
                className="route-leg-item"
              >
                <span className="route-leg-label">
                  {leg.dayNumber ? `Ngày ${leg.dayNumber}` : "Chặng đi"}
                  {leg.legLabel ? ` · ${leg.legLabel}` : ""}
                </span>
                <strong>
                  {leg.from} {"->"} {leg.to}
                </strong>
                <div className="route-leg-meta">
                  {leg.distanceKm ? <span className="route-leg-chip">{leg.distanceKm}</span> : null}
                  {leg.etaMin ? <span className="route-leg-chip">{leg.etaMin}</span> : null}
                  <span className={`route-mode-badge tone-${leg.modeTone}`}>{leg.modeBadge}</span>
                </div>
                {leg.directionUrl ? (
                  <a className="summary-link" href={leg.directionUrl} target="_blank" rel="noreferrer">
                    Chỉ đường
                  </a>
                ) : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {nextPrompt ? (
        <div className="summary-note">
          <span>Nhắc tiếp theo</span>
          <p>{nextPrompt}</p>
        </div>
      ) : null}
    </aside>
  );
}

export function ChatShell() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [serverConversations, setServerConversations] = useState<ConversationListItem[]>([]);
  const [draftConversations, setDraftConversations] = useState<ConversationListItem[]>([]);
  const [activeConversationKey, setActiveConversationKey] = useState<string | null>(null);
  const [conversationStates, setConversationStates] = useState<Record<string, ConversationViewState>>({});
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("Connecting to FastAPI...");
  const [error, setError] = useState<string | null>(null);
  const [conversationMutationPending, setConversationMutationPending] = useState(false);
  const [clockMs, setClockMs] = useState(() => Date.now());
  const activeConversationKeyRef = useRef<string | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    activeConversationKeyRef.current = activeConversationKey;
  }, [activeConversationKey]);

  const conversationItems = [...draftConversations, ...serverConversations];
  const activeConversationState = activeConversationKey
    ? conversationStates[activeConversationKey] ?? createConversationViewState()
    : createConversationViewState();
  const messages = activeConversationState.messages;
  const followUps = activeConversationState.followUps;
  const trace = activeConversationState.trace;
  const debugSteps = activeConversationState.debugSteps;
  const conversationStage = activeConversationState.conversationStage;
  const pendingMode = activeConversationState.pendingMode;
  const activeIsPending = activeConversationState.pendingStartedAt != null;
  const hasPendingConversations = Object.values(conversationStates).some((item) => item.pendingStartedAt != null);
  const pendingElapsedMs =
    activeConversationState.pendingStartedAt != null
      ? Math.max(0, clockMs - activeConversationState.pendingStartedAt)
      : 0;

  useEffect(() => {
    const element = messageListRef.current;
    if (!element) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!hasPendingConversations) {
      return;
    }

    const timer = window.setInterval(() => {
      setClockMs(Date.now());
    }, 180);

    return () => {
      window.clearInterval(timer);
    };
  }, [hasPendingConversations]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const session = await initSession();
        const items = await listConversations();
        if (cancelled) {
          return;
        }

        setPrincipal(session.principal);
        setServerConversations(items.map(toConversationListItem));
        setStatus("Anonymous session ready");

        if (items.length > 0) {
          const detail = await getConversation(items[0].id);
          if (cancelled) {
            return;
          }
          setActiveConversationKey(detail.id);
          const signals = extractConversationSignals(detail);
          setConversationStates((current) => ({
            ...current,
            [detail.id]: {
              conversationId: detail.id,
              messages: toDraftMessages(detail),
              followUps: signals.followUps,
              trace: signals.trace,
              debugSteps: signals.debugSteps,
              conversationStage: signals.conversationStage,
              pendingMode: null,
              pendingStartedAt: null,
            },
          }));
        }
      } catch (loadError) {
        if (cancelled) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Unable to reach the backend.");
        setStatus("FastAPI connection failed");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  function createDraftConversation() {
    const now = new Date().toISOString();
    const key = `draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const item: ConversationListItem = {
      key,
      conversationId: null,
      title: "New conversation",
      created_at: now,
      updated_at: now,
      latest_message_preview: null,
      message_count: 0,
    };
    setDraftConversations((current) => [item, ...current]);
    setConversationStates((current) => ({
      ...current,
      [key]: createConversationViewState(),
    }));
    return key;
  }

  async function refreshServerConversations() {
    const items = await listConversations();
    setServerConversations(items.map(toConversationListItem));
    return items;
  }

  async function focusConversationAfterRemoval(items: ConversationListItem[]) {
    const nextActive = items[0] ?? null;
    setActiveConversationKey(nextActive?.key ?? null);
    if (!nextActive) {
      return;
    }
    const localState = conversationStates[nextActive.key];
    if (localState?.messages.length || localState?.pendingStartedAt != null || !nextActive.conversationId) {
      return;
    }
    await loadConversationIntoState(nextActive.key, nextActive.conversationId);
  }

  async function loadConversationIntoState(key: string, conversationId: string) {
    const detail = await getConversation(conversationId);
    const signals = extractConversationSignals(detail);
    setConversationStates((current) => ({
      ...current,
      [key]: {
        conversationId: detail.id,
        messages: toDraftMessages(detail),
        followUps: signals.followUps,
        trace: signals.trace,
        debugSteps: signals.debugSteps,
        conversationStage: signals.conversationStage,
        pendingMode: null,
        pendingStartedAt: null,
      },
    }));
    return detail;
  }

  function updateConversationItemPreview(key: string, content: string) {
    const updatedAt = new Date().toISOString();
    const preview = content.trim();
    setDraftConversations((current) =>
      current.map((item) =>
        item.key === key
          ? {
              ...item,
              title: item.title === "New conversation" ? truncateMiddle(preview, 42) : item.title,
              updated_at: updatedAt,
              latest_message_preview: preview,
              message_count: item.message_count + 1,
            }
          : item,
      ),
    );
    setServerConversations((current) =>
      current.map((item) =>
        item.key === key
          ? {
              ...item,
              updated_at: updatedAt,
              latest_message_preview: preview,
              message_count: item.message_count + 1,
            }
          : item,
      ),
    );
  }

  async function handleConversationSelect(conversationKey: string) {
    setError(null);
    setStatus("Loading conversation...");
    setActiveConversationKey(conversationKey);

    const item = conversationItems.find((conversation) => conversation.key === conversationKey);
    if (!item) {
      setStatus("Conversation load failed");
      return;
    }

    const localState = conversationStates[conversationKey];
    if (localState?.messages.length || localState?.pendingStartedAt != null || !item.conversationId) {
      setStatus("Conversation loaded");
      return;
    }

    try {
      await loadConversationIntoState(conversationKey, item.conversationId);
      setStatus("Conversation loaded");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Conversation load failed.");
      setStatus("Conversation load failed");
    }
  }

  async function handleDeleteConversation(item: ConversationListItem, event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation();
    if (conversationMutationPending) {
      return;
    }
    const label = item.title === "New conversation" ? "this conversation" : `"${item.title}"`;
    if (!window.confirm(`Delete ${label}?`)) {
      return;
    }

    setConversationMutationPending(true);
    setError(null);
    setStatus("Deleting conversation...");

    try {
      let nextServerItems = serverConversations;
      const nextDraftItems = draftConversations.filter((draftItem) => draftItem.key !== item.key);

      if (item.conversationId) {
        await deleteConversation(item.conversationId);
        const refreshed = await refreshServerConversations();
        nextServerItems = refreshed.map(toConversationListItem);
      }

      setDraftConversations(nextDraftItems);
      setConversationStates((current) => {
        const updated = { ...current };
        delete updated[item.key];
        return updated;
      });

      const nextItems = [...nextDraftItems, ...nextServerItems].filter((conversation) => conversation.key !== item.key);
      if (activeConversationKeyRef.current === item.key) {
        await focusConversationAfterRemoval(nextItems);
      }
      setStatus(nextItems.length ? "Conversation deleted" : "Conversation history cleared");
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Conversation delete failed.");
      setStatus("Conversation delete failed");
    } finally {
      setConversationMutationPending(false);
    }
  }

  async function handleDeleteAllConversations() {
    if (conversationMutationPending) {
      return;
    }
    if (!window.confirm("Delete all saved conversation history?")) {
      return;
    }

    setConversationMutationPending(true);
    setError(null);
    setStatus("Clearing conversation history...");

    try {
      await deleteAllConversations();
      setServerConversations([]);
      setDraftConversations([]);
      setConversationStates({});
      setActiveConversationKey(null);
      setStatus("Conversation history cleared");
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Conversation reset failed.");
      setStatus("Conversation reset failed");
    } finally {
      setConversationMutationPending(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    let conversationKey = activeConversationKey;
    if (!content) {
      return;
    }
    if (!conversationKey) {
      conversationKey = createDraftConversation();
      setActiveConversationKey(conversationKey);
    }

    const activeState = conversationStates[conversationKey] ?? createConversationViewState();
    if (activeState.pendingStartedAt != null) {
      return;
    }
    const pendingMode = inferPendingMode(activeState, content);
    const pendingAssistantText = "";

    const optimisticId = `draft-${Date.now()}`;
    const assistantMessageId = `${optimisticId}-assistant`;
    setError(null);
    setStatus(pendingMode === "planning" ? "Đang lên lịch trình..." : "Đang kiểm tra thông tin đầu vào...");
    setDraft("");
    setConversationStates((current) => ({
      ...current,
      [conversationKey]: {
        ...(current[conversationKey] ?? createConversationViewState(activeState.conversationId)),
        conversationId: activeState.conversationId,
        messages: [
          ...((current[conversationKey]?.messages || activeState.messages) ?? []),
          { id: optimisticId, role: "user", content },
          { id: assistantMessageId, role: "assistant", content: pendingAssistantText, pending: true },
        ],
        followUps: [],
        trace: [],
        debugSteps: [],
        conversationStage: null,
        pendingMode,
        pendingStartedAt: Date.now(),
      },
    }));
    updateConversationItemPreview(conversationKey, content);

    const requestConversationId = activeState.conversationId;

    (async () => {
      try {
        let resolvedConversationId = requestConversationId || null;
        const response = await sendChatStream(content, requestConversationId || undefined, {
          onConversation: (event) => {
            resolvedConversationId = event.conversation_id || resolvedConversationId;
            setConversationStates((current) => ({
              ...current,
              [conversationKey]: {
                ...(current[conversationKey] ?? createConversationViewState(resolvedConversationId)),
                ...(current[conversationKey] ?? {}),
                conversationId: resolvedConversationId,
              },
            }));
          },
          onStage: (event) => {
            const progressText = progressCopyForStage(event.stage, event.status, Boolean(event.needs_replan));
            setConversationStates((current) => ({
              ...current,
              [conversationKey]: {
                ...(current[conversationKey] ?? createConversationViewState(resolvedConversationId)),
                conversationId: resolvedConversationId,
                messages: progressText
                  ? appendAssistantDelta(current[conversationKey]?.messages || [], assistantMessageId, progressText)
                  : (current[conversationKey]?.messages || []),
                followUps: event.follow_up_questions || [],
                trace: normalizeTrace(event.trace),
                debugSteps: current[conversationKey]?.debugSteps || [],
                conversationStage: event.conversation_stage || current[conversationKey]?.conversationStage || null,
                pendingMode: current[conversationKey]?.pendingMode || pendingMode,
                pendingStartedAt: current[conversationKey]?.pendingStartedAt ?? Date.now(),
              },
            }));
            if (activeConversationKeyRef.current === conversationKey || activeConversationKeyRef.current === resolvedConversationId) {
              setStatus(statusForStreamStage(event.stage, event.status, Boolean(event.needs_replan)));
            }
          },
          onAnswerStart: () => {
            setConversationStates((current) => ({
              ...current,
              [conversationKey]: {
                ...(current[conversationKey] ?? createConversationViewState(resolvedConversationId)),
                conversationId: resolvedConversationId,
                messages: replaceAssistantContent(current[conversationKey]?.messages || [], assistantMessageId, ""),
                followUps: current[conversationKey]?.followUps || [],
                trace: current[conversationKey]?.trace || [],
                debugSteps: current[conversationKey]?.debugSteps || [],
                conversationStage: current[conversationKey]?.conversationStage || null,
                pendingMode: current[conversationKey]?.pendingMode || pendingMode,
                pendingStartedAt: current[conversationKey]?.pendingStartedAt ?? Date.now(),
              },
            }));
            if (activeConversationKeyRef.current === conversationKey || activeConversationKeyRef.current === resolvedConversationId) {
              setStatus("Đang hiển thị câu trả lời...");
            }
          },
          onAnswerDelta: (delta) => {
            if (!delta) {
              return;
            }
            setConversationStates((current) => ({
              ...current,
              [conversationKey]: {
                ...(current[conversationKey] ?? createConversationViewState(resolvedConversationId)),
                conversationId: resolvedConversationId,
                messages: appendAssistantDelta(current[conversationKey]?.messages || [], assistantMessageId, delta),
                followUps: current[conversationKey]?.followUps || [],
                trace: current[conversationKey]?.trace || [],
                debugSteps: current[conversationKey]?.debugSteps || [],
                conversationStage: current[conversationKey]?.conversationStage || null,
                pendingMode: current[conversationKey]?.pendingMode || pendingMode,
                pendingStartedAt: current[conversationKey]?.pendingStartedAt ?? Date.now(),
              },
            }));
          },
        });
        resolvedConversationId = response.conversation_id || resolvedConversationId;

        await refreshServerConversations();

        setConversationStates((current) => {
          const currentState = current[conversationKey] ?? createConversationViewState(resolvedConversationId);
          const nextState: ConversationViewState = {
            conversationId: resolvedConversationId,
            messages: finalizeAssistantMessage(currentState.messages, assistantMessageId, response),
            followUps: response.follow_up_questions || [],
            trace: normalizeTrace(response.trace),
            debugSteps: response.debug_steps || [],
            conversationStage: response.conversation_stage || null,
            pendingMode: null,
            pendingStartedAt: null,
          };

          if (!resolvedConversationId || resolvedConversationId === conversationKey) {
            return {
              ...current,
              [conversationKey]: nextState,
            };
          }

          const updated = {
            ...current,
            [resolvedConversationId]: nextState,
          };
          delete updated[conversationKey];
          return updated;
        });

        if (resolvedConversationId && resolvedConversationId !== conversationKey) {
          setDraftConversations((current) => current.filter((item) => item.key !== conversationKey));
          setActiveConversationKey((current) => (current === conversationKey ? resolvedConversationId : current));
        }

        if (activeConversationKeyRef.current === conversationKey || activeConversationKeyRef.current === resolvedConversationId) {
          setStatus(response.conversation_stage === "intake" ? "Cần thêm một chút thông tin" : "Kế hoạch đã sẵn sàng");
        }
      } catch (submitError) {
        setConversationStates((current) => ({
          ...current,
          [conversationKey]: {
            ...(current[conversationKey] ?? createConversationViewState(requestConversationId)),
            conversationId: requestConversationId,
            messages: (current[conversationKey]?.messages || []).map((message) =>
              message.id === assistantMessageId
                ? {
                    ...message,
                    content: toFriendlySubmitError(submitError),
                    pending: false,
                  }
                : message,
            ),
            pendingMode: null,
            pendingStartedAt: null,
          },
        }));
        if (activeConversationKeyRef.current === conversationKey) {
          setError(toFriendlySubmitError(submitError));
          setStatus("Chat request failed");
        }
      }
    })();
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    if (activeIsPending || !draft.trim()) {
      return;
    }
    event.currentTarget.form?.requestSubmit();
  }

  const canShowEmptyState = !activeIsPending && messages.length === 0;
  const latestAssistantMessage = getLatestAssistantMessage(messages);
  const plannerSnapshot = buildPlannerSnapshot(latestAssistantMessage);
  const pendingSteps = buildPendingDebugSteps(pendingElapsedMs);
  const traceSteps = activeIsPending ? buildTraceSteps(pendingSteps) : buildTraceSteps(debugSteps);
  const shouldShowPlanningPanels = activeIsPending ? pendingMode === "planning" : !isIntakeStage(conversationStage);

  return (
    <main className="shell">
      <section className="workspace">
        <aside className="sidebar">
          <div className="sidebar-header">
            <h2>Chats</h2>
            <span>{conversationItems.length}</span>
          </div>

          <div className="sidebar-actions">
            <button
              className="ghost-button"
              type="button"
              disabled={conversationMutationPending}
              onClick={() => {
                const key = createDraftConversation();
                setActiveConversationKey(key);
                setStatus("Fresh conversation");
                setError(null);
              }}
            >
              New chat
            </button>

            <button
              className="ghost-button ghost-button-danger"
              type="button"
              disabled={conversationMutationPending || conversationItems.length === 0}
              onClick={handleDeleteAllConversations}
            >
              Clear all
            </button>
          </div>

          <div className="conversation-list">
            {conversationItems.length === 0 ? (
              <div className="empty-card">No saved conversations yet.</div>
            ) : (
              conversationItems.map((conversation) => (
                <div
                  key={conversation.key}
                  className={`conversation-item${conversation.key === activeConversationKey ? " is-active" : ""}`}
                >
                  <button
                    type="button"
                    className="conversation-select"
                    onClick={() => handleConversationSelect(conversation.key)}
                  >
                    <span className="conversation-title">{conversation.title}</span>
                    <span className="conversation-meta">{formatRelativeLabel(conversation.updated_at)}</span>
                    <span className="conversation-preview">
                      {conversation.latest_message_preview || "No preview yet"}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="conversation-delete"
                    disabled={conversationMutationPending}
                    onClick={(event) => handleDeleteConversation(conversation, event)}
                    aria-label={`Delete ${conversation.title}`}
                    title="Delete conversation"
                  >
                    Delete
                  </button>
                </div>
              ))
            )}
          </div>
        </aside>

        <section className="chat-panel">
          <div className="chat-header">
            <div className="chat-header-copy">
              <h1>Travel Planner</h1>
              <span className="chat-header-meta">
                {principal ? `${principal.type} · ${principal.id.slice(0, 8)}` : "Starting session"}
              </span>
            </div>
            <span className="chat-status">{status}</span>
          </div>

          <div className="chat-body">
            <div className="chat-main">
              <div ref={messageListRef} className="message-list">
                {canShowEmptyState ? (
                  <div className="empty-chat">
                    <article className="message-bubble assistant">
                      <span className="message-role">Assistant</span>
                      {renderMessageContent(STARTER_ASSISTANT_MESSAGE)}
                    </article>
                    <p>Bạn có thể trả lời ngắn như: &quot;3 ngày&quot;, &quot;2 ngày 1 đêm&quot; hoặc &quot;cuối tuần này&quot;.</p>
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

              {shouldShowPlanningPanels ? (
                <>
                  <div className="trace-panel">
                    <div className="trace-header">
                      <span>Thinking flow</span>
                      <span>
                        {activeIsPending
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
                      {!activeIsPending && trace.length === 0 ? <span className="trace-empty">No orchestration trace yet.</span> : null}
                    </div>
                  </div>

                  <div className="debug-panel">
                    <div className="trace-header">
                      <span>Step-by-step debug</span>
                      <span>
                        {activeIsPending
                          ? `Tracking · ${formatElapsedMs(pendingElapsedMs)}`
                          : debugSteps.length > 0
                            ? `${debugSteps.length} steps`
                            : "Idle"}
                      </span>
                    </div>
                    <div className="debug-steps">
                      {(activeIsPending ? pendingSteps : debugSteps).map((step) => (
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
                      {!activeIsPending && debugSteps.length === 0 ? (
                        <span className="trace-empty">No debug steps available yet.</span>
                      ) : null}
                    </div>
                  </div>
                </>
              ) : (
                <div className="trace-panel">
                  <div className="trace-header">
                    <span>Intake flow</span>
                    <span>{activeIsPending ? "Checking latest answer" : "Collecting details"}</span>
                  </div>
                  <div className="trace-list">
                    <span className="trace-chip status-needs_input">1. Chào hỏi và giới thiệu</span>
                    <span className="trace-chip status-needs_input">2. Hỏi từng câu để lấy đủ thông tin</span>
                    <span className={`trace-chip ${activeIsPending ? "status-running" : "status-waiting"}`}>
                      3. Chỉ chuyển sang planning khi đã đủ dữ liệu
                    </span>
                  </div>
                </div>
              )}

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

            <SummaryPanel
              snapshot={plannerSnapshot}
              followUps={followUps}
              isPending={activeIsPending}
              conversationStage={conversationStage}
              pendingMode={pendingMode}
            />
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              id="message"
              className="composer-input"
              rows={4}
              aria-label="Message"
              placeholder="Describe the trip you want the planner to build..."
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleComposerKeyDown}
            />
            <div className="composer-actions">
              {error ? (
                <span className="error-text">{error}</span>
              ) : (
                <span className="hint-text">Nhấn Enter để gửi, Shift+Enter để xuống dòng.</span>
              )}
              <div className="composer-action-buttons">
                <button className="submit-button" type="submit" disabled={activeIsPending || !draft.trim()}>
                  {activeIsPending ? (pendingMode === "planning" ? "Planning..." : "Checking info...") : "Send"}
                </button>
              </div>
            </div>
          </form>
        </section>
      </section>
    </main>
  );
}
