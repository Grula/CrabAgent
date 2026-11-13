// @vitest-environment jsdom
// F2: MeetingPanel persistence wiring.
// 1. restores a saved meeting on mount (title, participant chip, notes)
// 2. renders the ended state (no active composer) when the meeting already ended
// 3. debounces a saveMeeting(sessionId, state) call after the title changes
// 4. "End Meeting" calls endMeeting once and renders the returned summary + created tasks
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingPanel } from "./MeetingPanel";
import { endMeeting, getMeeting, saveMeeting, type MeetingNote } from "../api/meeting";
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

function inputs(): HTMLInputElement[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>("input"));
}

/** The header title field (placeholder "Meeting title…"), falling back to the first input. */
function titleInput(): HTMLInputElement | undefined {
  return inputs().find((input) => /title/i.test(input.placeholder ?? "")) ?? inputs()[0];
}

async function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await flushMicrotasks();
}

function endMeetingButton(): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find((button) =>
    textOf(button).includes("End Meeting"),
  );
}

/** Advances fake timers in steps so any reasonable debounce delay (<= ~11s) fires. */
async function advanceDebounce() {
  for (const step of [1000, 5000, 5000]) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(step);
    });
    if (vi.mocked(saveMeeting).mock.calls.length > 0) return;
  }
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

describe("MeetingPanel persistence", () => {
  it("restores the saved meeting on mount: title, participant chip and notes", async () => {
    vi.mocked(getMeeting).mockResolvedValue({
      title: "Weekly sync",
      participants: ["coder"],
      notes: [RESTORED_NOTE],
      status: "active",
    });

    await renderPanel("sess-1");

    expect(getMeeting).toHaveBeenCalledWith("sess-1");
    // Title is rendered in an editable field, not just plain text.
    expect(inputs().map((input) => input.value)).toContain("Weekly sync");
    // Participant value "coder" is shown with its display label from the agent profile.
    expect(textOf(document.body)).toContain("Coder");
    // Saved notes are restored.
    expect(textOf(document.body)).toContain("Discussed scope in the sync");
  });

  it("shows the ended state instead of the active composer when the meeting already ended", async () => {
    vi.mocked(getMeeting).mockResolvedValue({
      title: "Finished sync",
      participants: ["coder"],
      notes: [RESTORED_NOTE],
      status: "ended",
    });

    await renderPanel("sess-1");

    expect(getMeeting).toHaveBeenCalledWith("sess-1");
    expect(
      document.querySelector("textarea"),
      "the note composer must not render for an ended meeting",
    ).toBeNull();
    expect(
      endMeetingButton(),
      'the "End Meeting" button must not render for an ended meeting',
    ).toBeUndefined();
    expect(textOf(document.body)).toMatch(/ended|complete|finished|summary/i);
  });

  it("debounces a saveMeeting call with the updated title after the title changes", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    await renderPanel("sess-1");

    const input = titleInput();
    expect(input, "no header title input rendered").toBeTruthy();
    await setInputValue(input!, "Renamed meeting");

    expect(saveMeeting, "saveMeeting must be debounced, not called on every keystroke").not.toHaveBeenCalled();

    await advanceDebounce();

    expect(saveMeeting).toHaveBeenCalled();
    const saveCalls = vi.mocked(saveMeeting).mock.calls;
    const lastCall = saveCalls[saveCalls.length - 1];
    expect(lastCall[0]).toBe("sess-1");
    expect(lastCall[1].title).toBe("Renamed meeting");
    expect(lastCall[1].participants).toEqual([]);
    expect(Array.isArray(lastCall[1].notes)).toBe(true);
  });

  it('ends the meeting through the API and renders the returned summary and created tasks', async () => {
    vi.mocked(getMeeting).mockResolvedValue({
      title: "Release sync",
      participants: ["coder"],
      notes: [RESTORED_NOTE],
      status: "active",
    });
    vi.mocked(endMeeting).mockResolvedValue({
      session_id: "sess-1",
      summary: "Discussed the release plan and agreed on scope.",
      created_tasks: [
        { id: 7, title: "Write the smoke tests", assignee: "coder", priority: "high" },
        { id: 8, title: "Ship the release notes", assignee: "coder", priority: "medium", deadline: "2026-12-01" },
      ],
      status: "ended",
    });

    await renderPanel("sess-1");

    const button = endMeetingButton();
    expect(button, 'no "End Meeting" button rendered').toBeTruthy();
    await act(async () => {
      button!.click();
    });
    await flush();

    expect(endMeeting).toHaveBeenCalledTimes(1);
    const endCalls = vi.mocked(endMeeting).mock.calls;
    const [sessionArg, stateArg] = endCalls[0];
    expect(sessionArg).toBe("sess-1");
    expect(stateArg.title).toBe("Release sync");
    expect(stateArg.participants).toEqual(["coder"]);
    expect(stateArg.notes.map((note) => note.content)).toContain("Discussed scope in the sync");

    const bodyText = textOf(document.body);
    expect(bodyText).toContain("Discussed the release plan and agreed on scope.");
    expect(bodyText).toContain("Write the smoke tests");
    expect(bodyText).toContain("Ship the release notes");
  });
});
