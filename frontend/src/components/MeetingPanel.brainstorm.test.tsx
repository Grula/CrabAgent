// @vitest-environment jsdom
// MeetingPanel brainstorm UI.
// 1. renders a Brainstorm control (text/aria-label "Brainstorm" or 💡) plus a topic field
// 2. topic typed + participant selected -> click -> brainstormMeeting(sessionId, topic, participants) once
// 3. the resolved ideas and reactions are rendered (idea text, verdict, comment)
// 4. no participants selected -> clicking Brainstorm does not call the API
//
// DOM contract encoded here (deliberately narrow, so the markup stays free-form):
//  - the topic field is an <input>/<textarea> whose placeholder/aria-label/name/title
//    matches /brainstorm|topic|idea/i
//  - the trigger is a button (role=button/tab/menuitem) whose text OR aria-label/title
//    matches /brainstorm|💡/i
//  - participants are the panel's own participant values, e.g. restored from getMeeting()
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingPanel } from "./MeetingPanel";
import {
  brainstormMeeting,
  endMeeting,
  getMeeting,
  saveMeeting,
  type BrainstormResult,
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
    {
      id: 3,
      name: "writer",
      display_name: "Writer",
      role: "Documentation specialist",
      goal: "Write docs",
      backstory: "Careful editor",
      model: "gpt-4o",
      allow_delegation: false,
      enabled: true,
      icon: "✍️",
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
  brainstormMeeting: vi.fn(),
}));

vi.mock("../api/agents", () => ({
  listAgentProfiles: vi.fn(async () => AGENTS),
}));

vi.mock("../api/sessions", () => ({
  sendPrompt: vi.fn(async () => ({})),
}));

const TOPIC = "Adopt a feature-flag rollout";
const IDEA = "Gate the rollout behind a feature flag";
const COMMENT = "Yes, ship it behind a flag";
const EMPTY_RESULT: BrainstormResult = { session_id: "sess-1", ideas: [], reactions: [] };
const FILLED_RESULT: BrainstormResult = {
  session_id: "sess-1",
  ideas: [{ agent: "coder", ideas: [IDEA] }],
  reactions: [
    {
      agent: "coder",
      reactions: [{ idea_index: 0, verdict: "agree", comment: COMMENT }],
    },
  ],
};

let container: HTMLDivElement;
let root: Root;

const textOf = (el: Element | null | undefined) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Lets queued macrotasks (real timers) run. */
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
  await flush();
}

function fields(): (HTMLInputElement | HTMLTextAreaElement)[] {
  return Array.from(document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea"));
}

/** The brainstorm topic field: labelled by placeholder/aria-label/name/title. */
function topicField(): HTMLInputElement | HTMLTextAreaElement | undefined {
  return fields().find((field) => {
    const label = [
      field.placeholder,
      field.getAttribute("aria-label"),
      field.getAttribute("name"),
      field.getAttribute("title"),
    ]
      .filter(Boolean)
      .join(" ");
    return /brainstorm|topic|idea/i.test(label);
  });
}

function controlLabel(el: Element): string {
  const meta = [el.getAttribute("aria-label"), el.getAttribute("title"), el.getAttribute("name")]
    .filter(Boolean)
    .join(" ");
  return `${textOf(el)} ${meta}`.trim();
}

/** The Brainstorm trigger: a clickable control mentioning Brainstorm or showing 💡. */
function brainstormControl(): HTMLElement | undefined {
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>(
      "button, [role='button'], [role='tab'], [role='menuitem'], [role='menuitemcheckbox']",
    ),
  );
  return candidates.find((el) => /brainstorm|💡/i.test(controlLabel(el)));
}

async function setFieldValue(field: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  await act(async () => {
    setter?.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await flushMicrotasks();
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await flush();
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom does not implement scrollIntoView; MeetingPanel calls it on mount.
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  vi.mocked(listAgentProfiles).mockResolvedValue(AGENTS);
  vi.mocked(getMeeting).mockResolvedValue({ title: "", participants: [], notes: [], status: "active" });
  vi.mocked(saveMeeting).mockResolvedValue({ title: "", participants: [], notes: [], status: "active" });
  vi.mocked(brainstormMeeting).mockResolvedValue(EMPTY_RESULT);
  document.body.innerHTML = "";
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  container?.remove();
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

describe("MeetingPanel brainstorm", () => {
  it("renders a Brainstorm control and a topic field", async () => {
    await renderPanel("sess-1");

    expect(
      brainstormControl(),
      'no Brainstorm control rendered (no button/label whose text or aria-label matches /brainstorm|💡/)',
    ).toBeTruthy();
    expect(
      topicField(),
      "no brainstorm topic field rendered (no input/textarea labelled with topic/brainstorm/ideas)",
    ).toBeTruthy();
  });

  it("calls brainstormMeeting(sessionId, topic, participants) once when a topic is typed and a participant is selected", async () => {
    vi.mocked(getMeeting).mockResolvedValue({
      title: "Retro sync",
      participants: ["coder"],
      notes: [],
      status: "active",
    });

    await renderPanel("sess-1");

    const field = topicField();
    expect(field, "no brainstorm topic field rendered").toBeTruthy();
    await setFieldValue(field!, TOPIC);

    const trigger = brainstormControl();
    expect(trigger, "no Brainstorm control rendered").toBeTruthy();
    await click(trigger!);

    expect(brainstormMeeting).toHaveBeenCalledTimes(1);
    const [sessionArg, topicArg, participantsArg] = vi.mocked(brainstormMeeting).mock.calls[0];
    expect(sessionArg).toBe("sess-1");
    expect(topicArg).toBe(TOPIC);
    expect(participantsArg).toEqual(["coder"]);
  });

  it("renders the returned ideas together with the agents' reactions", async () => {
    vi.mocked(getMeeting).mockResolvedValue({
      title: "Retro sync",
      participants: ["coder"],
      notes: [],
      status: "active",
    });
    vi.mocked(brainstormMeeting).mockResolvedValue(FILLED_RESULT);

    await renderPanel("sess-1");

    const field = topicField();
    expect(field, "no brainstorm topic field rendered").toBeTruthy();
    await setFieldValue(field!, TOPIC);

    const trigger = brainstormControl();
    expect(trigger, "no Brainstorm control rendered").toBeTruthy();
    await click(trigger!);

    const body = textOf(document.body);
    expect(body, "the brainstormed idea is not rendered").toContain(IDEA);
    expect(body, "the reaction comment is not rendered").toContain(COMMENT);
    expect(body, "the reaction verdict is not rendered").toMatch(/agree/i);
  });

  it("does not call brainstormMeeting when no participant is selected", async () => {
    await renderPanel("sess-1");

    const field = topicField();
    expect(field, "no brainstorm topic field rendered").toBeTruthy();
    await setFieldValue(field!, TOPIC);

    const trigger = brainstormControl();
    expect(trigger, "no Brainstorm control rendered").toBeTruthy();
    const disabled =
      (trigger as HTMLButtonElement).disabled === true ||
      trigger!.getAttribute("aria-disabled") === "true";
    if (!disabled) {
      await click(trigger!);
    }

    expect(
      brainstormMeeting,
      "brainstormMeeting must not be called without a selected participant (the button must be disabled or a no-op)",
    ).not.toHaveBeenCalled();
  });
});
