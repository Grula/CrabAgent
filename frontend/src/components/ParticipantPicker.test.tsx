// @vitest-environment jsdom
// Contract tests for ParticipantPicker (M-2).
// The component is a controlled multi-select: chips for selected values, a
// FloatingMenu (portal into document.body) listing all options, a search
// filter, and per-chip remove buttons.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";
import { ParticipantPicker } from "./ParticipantPicker";

type ParticipantOption = { value: string; label: string; icon: string };
type OnChange = (next: string[]) => void;

const CODER: ParticipantOption = { value: "coder", label: "Coder", icon: "💻" };
const RESEARCHER: ParticipantOption = {
  value: "researcher",
  label: "Researcher",
  icon: "🔬",
};
const ALL_LABELS = [CODER.label, RESEARCHER.label];

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function renderPicker(
  value: string[],
  options: ParticipantOption[],
  onChange: Mock<OnChange> = vi.fn<OnChange>(),
): Mock<OnChange> {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(
      <ParticipantPicker value={value} options={options} onChange={onChange} />,
    );
  });
  return onChange;
}

/** The trigger is the first control rendered by the picker. */
function trigger(): HTMLElement {
  const el = container!.querySelector<HTMLElement>("button, [role='button']");
  expect(el, "expected the picker to render a trigger button").toBeTruthy();
  return el!;
}

/** FloatingMenu portals its menu in as a direct child of <body> with class "fixed". */
function portal(): HTMLElement | null {
  return document.querySelector<HTMLElement>("body > .fixed");
}

function requirePortal(): HTMLElement {
  const el = portal();
  expect(
    el,
    "expected the dropdown to be rendered through FloatingMenu into document.body",
  ).toBeTruthy();
  return el!;
}

/** Clickable option rows inside the portal (button or accessible option role). */
function optionRows(scope: HTMLElement): HTMLElement[] {
  return Array.from(
    scope.querySelectorAll<HTMLElement>(
      "button, [role='option'], [role='menuitem'], [role='menuitemcheckbox']",
    ),
  );
}

function rowByLabel(scope: HTMLElement, label: string): HTMLElement | null {
  return (
    optionRows(scope).find((row) => (row.textContent ?? "").includes(label)) ??
    null
  );
}

/** A selected row is marked either semantically or with a visible check mark. */
function isMarkedSelected(row: HTMLElement): boolean {
  return (
    row.getAttribute("aria-selected") === "true" ||
    row.getAttribute("aria-checked") === "true" ||
    row.getAttribute("aria-pressed") === "true" ||
    (row.textContent ?? "").includes("✓") ||
    row.querySelector("svg") !== null
  );
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await act(async () => {
    await Promise.resolve();
  });
}

async function openDropdown() {
  await click(trigger());
}

async function typeInto(input: HTMLInputElement, text: string) {
  const setValue = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setValue.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    await Promise.resolve();
  });
}

function searchInput(): HTMLInputElement {
  const input = requirePortal().querySelector<HTMLInputElement>("input");
  expect(input, "expected a search input inside the dropdown").toBeTruthy();
  return input!;
}

/** Candidate remove (×) controls inside the trigger. */
function removeCandidates(): HTMLElement[] {
  return Array.from(
    container!.querySelectorAll<HTMLElement>("button, [role='button']"),
  ).filter((el) => {
    const text = (el.textContent ?? "").trim();
    const meta = `${el.getAttribute("aria-label") ?? ""} ${
      el.getAttribute("title") ?? ""
    }`;
    return /^[×✕✖x]$/i.test(text) || /remove/i.test(meta);
  });
}

/** Nearest ancestor that mentions exactly one chip label (i.e. one chip). */
function nearestSingleLabel(el: HTMLElement, labels: string[]): string | null {
  let cur = el.parentElement;
  while (cur && cur !== container) {
    const text = cur.textContent ?? "";
    const hits = labels.filter((label) => text.includes(label));
    if (hits.length === 1) return hits[0];
    if (hits.length > 1) return null;
    cur = cur.parentElement;
  }
  return null;
}

function removeButtonFor(label: string): HTMLElement | null {
  const candidates = removeCandidates();
  const explicitlyNamed = candidates.find((el) => {
    const meta = `${el.getAttribute("aria-label") ?? ""} ${
      el.getAttribute("title") ?? ""
    }`;
    return /remove/i.test(meta) && meta.includes(label);
  });
  if (explicitlyNamed) return explicitlyNamed;
  return (
    candidates.find((el) => nearestSingleLabel(el, ALL_LABELS) === label) ?? null
  );
}

beforeEach(() => {
  (
    globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
  document.body.innerHTML = "";
});

afterEach(() => {
  if (root) act(() => root!.unmount());
  container?.remove();
  root = null;
  container = null;
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

describe("ParticipantPicker", () => {
  it("renders a chip with icon and label for each selected value", () => {
    renderPicker(["coder"], [CODER]);

    expect(container!.textContent).toContain("Coder");
    expect(container!.textContent).toContain("💻");
  });

  it('shows the placeholder "Add participants…" when nothing is selected', () => {
    renderPicker([], [CODER, RESEARCHER]);

    expect(trigger().textContent).toContain("Add participants…");
  });

  it("lists every option with a check mark on selected ones and appends a non-selected option on click", async () => {
    const onChange = renderPicker(["coder"], [CODER, RESEARCHER]);

    await openDropdown();
    const scope = requirePortal();

    const coderRow = rowByLabel(scope, CODER.label);
    const researcherRow = rowByLabel(scope, RESEARCHER.label);
    expect(coderRow, "expected a dropdown row for Coder").toBeTruthy();
    expect(researcherRow, "expected a dropdown row for Researcher").toBeTruthy();
    expect(isMarkedSelected(coderRow!)).toBe(true);
    expect(isMarkedSelected(researcherRow!)).toBe(false);

    await click(researcherRow!);

    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as string[];
    expect([...next].sort()).toEqual(["coder", "researcher"]);
  });

  it("removes a value when its chip remove (×) button is clicked", async () => {
    const onChange = renderPicker(["coder", "researcher"], [CODER, RESEARCHER]);

    const remove = removeButtonFor(CODER.label);
    expect(remove, "expected a remove (×) button on the Coder chip").toBeTruthy();

    await click(remove!);

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toEqual(["researcher"]);
  });

  it("filters options case-insensitively and reports when nothing matches", async () => {
    renderPicker([], [CODER, RESEARCHER]);

    await openDropdown();
    expect(rowByLabel(requirePortal(), CODER.label)).toBeTruthy();
    expect(rowByLabel(requirePortal(), RESEARCHER.label)).toBeTruthy();

    await typeInto(searchInput(), "RESE");

    const filtered = requirePortal();
    expect(rowByLabel(filtered, RESEARCHER.label)).toBeTruthy();
    expect(rowByLabel(filtered, CODER.label)).toBeNull();

    await typeInto(searchInput(), "zzz");

    expect(requirePortal().textContent).toContain("No matching agents");
  });

  it('is disabled and shows "No agents" when there are no options', () => {
    renderPicker([], []);

    const el = trigger();
    expect(el.textContent).toContain("No agents");
    const disabled =
      (el as HTMLButtonElement).disabled === true ||
      el.getAttribute("aria-disabled") === "true";
    expect(disabled).toBe(true);
  });
});
