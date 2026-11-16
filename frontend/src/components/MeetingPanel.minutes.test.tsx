// @vitest-environment jsdom
// M2: render persisted meeting minutes on reload.
// 1. a persisted `minutes` value returned by getMeeting() is rendered in the
//    ended-state summary area instead of "Summary unavailable."
// 2. when the stored meeting carries no minutes, the existing placeholder is kept
//    (the `minutes ?? ""` fallback must not invent text).
// Fixtures/mocks mirror MeetingPanel.persistence.test.tsx exactly.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingPanel } from "./MeetingPanel";
import {
  endMeeting,
  getMeeting,
  saveMeeting,
  type MeetingNote,
  type MeetingState,
} from "../api/meeting";
import { listAgentProfiles } from "../api/agents";

const { AGENTS } = vi.hoisted(() => ({
  AGENTS: [
    {
      id: 2,
      name: "coder",
      display_name: "Coder",
      role: "Implementation specialist",
      goal: "Write code",
      backstory: "Seasoned engineer",
      model: "gpt-4o",
      allow_delegation: false,
      enabled: true,
      icon: "💻",
      is_default: false,
      tools: [],
      tool_permissions: {},
      created_at: "2026-01-01T00:00:00Z",
    },
  ],
}));

vi.mock("../api/meeting", () => ({
  getMeeting: vi.fn(),
  saveMeeting: vi.fn(),
  endMeeting: vi.fn(),
}));

vi.mock("../api/agents", () => ({
  listAgentProfiles: vi.fn(async () => AGENTS),
}));

vi.mock("../api/sessions", () => ({
  sendPrompt: vi.fn(async () => ({})),
}));

const RESTORED_NOTE: MeetingNote = {
  id: "n-1",
  timestamp: "10:15",
  speaker: "Coder",
  content: "Discussed scope in the sync",
};

const PERSISTED_MINUTES = "Persisted meeting minutes";

/**
 * The `minutes` field is added to MeetingState/EndMeetingResult by the M2
 * contract; the cast keeps this suite compiling before that type change lands.
 */
function persistedMeeting(extra: { minutes?: string }): MeetingState {
  return {
    title: "Ended sync",
    participants: ["coder"],
    notes: [RESTORED_NOTE],
    status: "ended",
    ...extra,
  } as unknown as MeetingState;
}

let container: HTMLDivElement;
let root: Root;

const textOf = (el: Element | null | undefined) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();

/** Drains promise callbacks without depending on real timers. */
async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Lets queued macrotasks (real timers) run. Never used with fake timers. */
async function flush() {
  await flushMicrotasks();
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderPanel(sessionId = "sess-1") {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<MeetingPanel sessionId={sessionId} />);
  });
  await flushMicrotasks();
  if (!vi.isFakeTimers()) await flush();
}

/** The ended-state summary paragraph, i.e. any <p> rendering the given text. */
function paragraphContaining(text: string): HTMLParagraphElement | undefined {
  return Array.from(document.querySelectorAll<HTMLParagraphElement>("p")).find((p) =>
    textOf(p).includes(text),
  );
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom does not implement scrollIntoView; MeetingPanel calls it on mount.
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  vi.mocked(listAgentProfiles).mockResolvedValue(AGENTS);
  vi.mocked(getMeeting).mockResolvedValue({
    title: "",
    participants: [],
    notes: [],
    status: "active",
  });
  vi.mocked(saveMeeting).mockResolvedValue({
    title: "",
    participants: [],
    notes: [],
    status: "active",
  });
  document.body.innerHTML = "";
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  container?.remove();
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("MeetingPanel persisted minutes", () => {
  it("restores the persisted minutes into the ended-state summary on mount", async () => {
    vi.mocked(getMeeting).mockResolvedValue(persistedMeeting({ minutes: PERSISTED_MINUTES }));

    await renderPanel("sess-1");

    expect(getMeeting).toHaveBeenCalledWith("sess-1");

    // The reloaded ended meeting must show its stored minutes, not a placeholder.
    expect(
      paragraphContaining(PERSISTED_MINUTES),
      "the ended-state summary paragraph must render the persisted minutes",
    ).toBeTruthy();
    expect(textOf(document.body), "the placeholder must not replace persisted minutes").not.toContain(
      "Summary unavailable.",
    );

    // Restoring minutes must not disturb the rest of the restored state.
    expect(textOf(document.body)).toContain("Discussed scope in the sync");
    expect(endMeeting).not.toHaveBeenCalled();
  });

  it("keeps the placeholder when the stored meeting has no minutes", async () => {
    vi.mocked(getMeeting).mockResolvedValue(persistedMeeting({}));

    await renderPanel("sess-1");

    expect(getMeeting).toHaveBeenCalledWith("sess-1");
    expect(
      textOf(document.body),
      "a stored meeting without minutes must fall back to the placeholder",
    ).toContain("Summary unavailable.");
  });
});
