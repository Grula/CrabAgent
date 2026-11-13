// @vitest-environment jsdom
// M-3: MeetingPanel must offer an agent picker (ParticipantPicker) instead of the old
// free-text "Participants…" input, render selected agents as removable chips, and include
// the selected participants as a line in the "End Meeting" prompt.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingPanel } from "./MeetingPanel";
import { listAgentProfiles } from "../api/agents";

const { AGENTS } = vi.hoisted(() => {
  const agents = [
    {
      id: 1,
      name: "researcher",
      display_name: "Researcher",
      role: "Research specialist",
      goal: "Find sources",
      backstory: "Seasoned researcher",
      model: "gpt-4o",
      allow_delegation: false,
      enabled: true,
      icon: "🔍",
      is_default: false,
      tools: [],
      tool_permissions: {},
      created_at: "2026-01-01T00:00:00Z",
    },
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
  ];
  return { AGENTS: agents };
});

vi.mock("../api/agents", () => ({
  listAgentProfiles: vi.fn(async () => AGENTS),
}));

vi.mock("../api/sessions", () => ({
  sendPrompt: vi.fn(async () => ({})),
}));

const RESEARCHER = "Researcher";

let container: HTMLDivElement;
let root: Root;

const textOf = (el: Element | null | undefined) =>
  (el?.textContent ?? "").replace(/\s+/g, " ").trim();

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  await act(async () => {
    await Promise.resolve();
  });
}

async function renderPanel(
  props: { sessionId?: string; className?: string; onPrompt?: (text: string) => void } = {},
) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MeetingPanel
        sessionId={props.sessionId ?? "sess-1"}
        className={props.className}
        onPrompt={props.onPrompt}
      />,
    );
  });
  await flush();
}

/** Short elements that carry the agent label: dropdown options, list rows or buttons. */
function optionTargets(name: string): HTMLElement[] {
  const nodes = Array.from(
    document.body.querySelectorAll<HTMLElement>(
      '[role="option"],[role="menuitemcheckbox"],[role="menuitem"],[role="checkbox"],[role="listbox"] > *,button,li,label,a,div,span',
    ),
  );
  return nodes.filter((el) => {
    const text = textOf(el);
    const aria = el.getAttribute("aria-label") ?? "";
    return (text.includes(name) || aria.includes(name)) && text.length <= name.length + 24;
  });
}

/** Opens the picker dropdown if its options are not already in the DOM. */
async function openPickerIfNeeded(name: string) {
  if (optionTargets(name).length > 0) return;
  const triggers = Array.from(
    document.body.querySelectorAll<HTMLElement>(
      'button,[role="combobox"],[aria-haspopup],[aria-expanded]',
    ),
  );
  const trigger = triggers.find((el) => {
    const hint = `${textOf(el)} ${el.getAttribute("aria-label") ?? ""} ${el.getAttribute("placeholder") ?? ""}`;
    return /participant|agent|select|add|people|who/i.test(hint);
  });
  if (!trigger) return;
  await act(async () => {
    trigger.click();
  });
  await flush();
}

async function selectAgent(name: string) {
  await openPickerIfNeeded(name);
  const target = optionTargets(name).sort(
    (a, b) => textOf(a).length - textOf(b).length,
  )[0];
  expect(target, `no selectable option labelled "${name}"`).toBeTruthy();
  await act(async () => {
    target.click();
  });
  await flush();
}

/** A chip is a short element holding the label plus a distinct remove/clear control. */
function removeControl(el: HTMLElement, name: string): HTMLElement | undefined {
  const controls = Array.from(
    el.querySelectorAll<HTMLElement>('button,[role="button"],[aria-label],[title]'),
  );
  return controls.find((control) => {
    if (control === el) return false;
    const text = textOf(control);
    const aria = control.getAttribute("aria-label") ?? "";
    const hint = `${text} ${aria} ${control.getAttribute("title") ?? ""}`.trim();
    // Explicit remove/clear affordance, e.g. aria-label="Remove Researcher" or "×".
    if (/remove|delete|clear|dismiss|unselect/i.test(hint)) return true;
    // An element named exactly after the agent is the option itself, not a remove control.
    if (text === name || aria === name) return false;
    if (text === "×" || text === "✕" || text === "x") return true;
    return control.querySelector("svg.lucide-x, svg.lucide-x-circle, svg.lucide-circle-x") !== null;
  });
}

function chipFor(name: string): HTMLElement | undefined {
  const nodes = Array.from(
    document.body.querySelectorAll<HTMLElement>('div,span,li,button,[role="listitem"]'),
  );
  const matches = nodes.filter((el) => textOf(el).includes(name) && removeControl(el, name));
  return matches.sort((a, b) => textOf(a).length - textOf(b).length)[0];
}

function endMeetingButton(): HTMLButtonElement | undefined {
  return Array.from(document.body.querySelectorAll<HTMLButtonElement>("button")).find((button) =>
    textOf(button).includes("End Meeting"),
  );
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom does not implement scrollIntoView; MeetingPanel calls it on mount.
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  vi.mocked(listAgentProfiles).mockResolvedValue(AGENTS);
  vi.mocked(listAgentProfiles).mockClear();
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

describe("MeetingPanel participant picker", () => {
  it("renders an enabled agent's display name as a selectable option", async () => {
    await renderPanel();
    await openPickerIfNeeded(RESEARCHER);

    expect(listAgentProfiles).toHaveBeenCalled();
    expect(document.body.textContent).toContain(RESEARCHER);
    expect(optionTargets(RESEARCHER).length).toBeGreaterThan(0);
  });

  it("adds a selected agent as a removable participant chip", async () => {
    await renderPanel();
    expect(chipFor(RESEARCHER)).toBeUndefined();

    await selectAgent(RESEARCHER);

    const chip = chipFor(RESEARCHER);
    expect(chip, `expected a removable chip labelled "${RESEARCHER}"`).toBeTruthy();
    expect(textOf(chip)).toContain(RESEARCHER);
  });

  it("includes the selected participant label in the End Meeting prompt", async () => {
    const onPrompt = vi.fn<(text: string) => void>();
    await renderPanel({ onPrompt });

    await selectAgent(RESEARCHER);

    const button = endMeetingButton();
    expect(button, 'no "End Meeting" button rendered').toBeTruthy();
    await act(async () => {
      button!.click();
    });
    await flush();

    expect(onPrompt).toHaveBeenCalledTimes(1);
    const prompt = onPrompt.mock.calls[onPrompt.mock.calls.length - 1]?.[0] as string;
    expect(prompt).toContain(RESEARCHER);
    expect(prompt).toMatch(/Participants:\s*Researcher/);
  });

  it('still renders the panel and shows "No agents" when listAgentProfiles rejects', async () => {
    vi.mocked(listAgentProfiles).mockRejectedValueOnce(new Error("network down"));

    await renderPanel();

    expect(document.body.textContent).toContain("No agents");
    expect(endMeetingButton()).toBeTruthy();
  });
});
