// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UsagePage from "./UsagePage";
import type { SessionUsage, TokenUsageOverview } from "../api/tokenUsage";

const mocks = vi.hoisted(() => ({
  getOverview: vi.fn(),
  getSessionsUsage: vi.fn(),
  getSessionUsageDetail: vi.fn(),
  getWorkspacesUsage: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: {} }));
vi.mock("../components/UsageHeatmap", () => ({ default: () => <div>{"Heatmap"}</div> }));
vi.mock("../components/charts/MeasuredChartContainer", () => ({ MeasuredChartContainer: () => <div>{"Chart"}</div> }));
vi.mock("../api/tokenUsage", () => ({
  getOverview: mocks.getOverview,
  getSessionsUsage: mocks.getSessionsUsage,
  getSessionUsageDetail: mocks.getSessionUsageDetail,
  getWorkspacesUsage: mocks.getWorkspacesUsage,
}));

const overview = {
  total_tokens: 200000, prompt_tokens: 130000, cached_tokens: 60000, non_cached_tokens: 70000,
  completion_tokens: 70000, reasoning_tokens: 0, cache_hit_rate: 0.46, total_calls: 25, today_tokens: 1000,
  sessions_count: 2, hourly: false, trend: [], daily: [],
  by_agent: [
    { agent_name: "researcher", total_tokens: 120000, prompt_tokens: 80000, non_cached_tokens: 40000, cached_tokens: 40000, completion_tokens: 40000, reasoning_tokens: 0, cache_hit_rate: 0.5, calls: 12 },
    { agent_name: "coder", total_tokens: 60000, prompt_tokens: 40000, non_cached_tokens: 7300, cached_tokens: 32700, completion_tokens: 20000, reasoning_tokens: 0, cache_hit_rate: 0.82, calls: 9 },
    { agent_name: "scout", total_tokens: 20000, prompt_tokens: 10000, non_cached_tokens: 10000, cached_tokens: 0, completion_tokens: 10000, reasoning_tokens: 0, cache_hit_rate: 0, calls: 4 },
  ],
  by_model: [{ model: "gpt-test", total_tokens: 200000, prompt_tokens: 130000, cached_tokens: 60000, completion_tokens: 70000, calls: 25 }],
} as unknown as TokenUsageOverview;

const session = {
  session_id: "s1", title: "Agent session", total_tokens: 50000, prompt_tokens: 30000, cached_tokens: 15000,
  non_cached_tokens: 15000, completion_tokens: 20000, reasoning_tokens: 0, cache_hit_rate: 0.5, calls: 3,
  last_active: "2025-05-02T11:00:00Z", created_at: "2025-05-02T10:00:00Z",
} as SessionUsage;

let container: HTMLDivElement;
let root: Root;

async function render() { await act(async () => { root.render(<UsagePage />); }); }
async function click(el: HTMLElement) { await act(async () => { el.click(); }); }
function buttons(): HTMLButtonElement[] { return [...container.querySelectorAll("button")]; }
function button(text: string) { const found = buttons().find((item) => item.textContent?.trim() === text); if (!found) throw new Error(`Missing button: ${text}`); return found; }
function agentRow(name: string) { const found = buttons().find((item) => item.getAttribute("aria-pressed") !== null && (item.textContent ?? "").includes(name)); if (!found) throw new Error(`Missing agent row: ${name}`); return found; }
function sessionShareCells(): string[] { return [...container.querySelectorAll("tbody tr")].map((row) => (row.querySelectorAll("td")[3]?.textContent ?? "").trim()); }

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  mocks.getOverview.mockReset().mockResolvedValue(overview);
  mocks.getSessionsUsage.mockReset().mockResolvedValue({ sessions: [session], total: 1 });
  mocks.getSessionUsageDetail.mockReset().mockResolvedValue(null);
  mocks.getWorkspacesUsage.mockReset().mockResolvedValue([]);
});

afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

describe("usage page per-agent breakdown", () => {
  it("lists every agent with tokens, cache rate and calls", async () => {
    await render();
    const html = container.textContent ?? "";
    expect(html).toContain("researcher");
    expect(html).toContain("coder");
    expect(html).toContain("scout");
    expect(html).toContain("120.0K");
    expect(html).toContain("60.0K");
    expect(html).toContain("82%");
    expect(html).toContain("9 calls");
    expect(html).toContain("60%");
  });

  it("filters the session list when an agent row is selected and clears on second click", async () => {
    await render();
    expect(mocks.getSessionsUsage).toHaveBeenLastCalledWith(15, 0, 30, "", "");
    await click(agentRow("researcher"));
    expect(mocks.getSessionsUsage).toHaveBeenLastCalledWith(15, 0, 30, "", "researcher");
    expect(container.textContent).toContain("Filtered by agent");
    await click(agentRow("researcher"));
    expect(mocks.getSessionsUsage).toHaveBeenLastCalledWith(15, 0, 30, "", "");
    expect(container.textContent).not.toContain("Filtered by agent");
  });

  it("loads more filtered sessions with the agent and a non-zero offset", async () => {
    mocks.getSessionsUsage.mockImplementation((_limit: number, offset: number, _days: number, _workspace: string, agentName: string) => Promise.resolve({
      sessions: [{ ...session, session_id: `s-${offset}` }],
      total: agentName ? 4 : 9,
    }));
    await render();
    await click(agentRow("researcher"));
    expect(mocks.getSessionsUsage).toHaveBeenLastCalledWith(15, 0, 30, "", "researcher");
    await click(button("Load more sessions"));
    expect(mocks.getSessionsUsage).toHaveBeenLastCalledWith(15, 1, 30, "", "researcher");
  });

  it("shows the filtered session total in the header while an agent is selected", async () => {
    mocks.getSessionsUsage.mockImplementation((_limit: number, _offset: number, _days: number, _workspace: string, agentName: string) => Promise.resolve({
      sessions: [session],
      total: agentName ? 1 : 2,
    }));
    await render();
    expect(container.textContent).toContain("Session Details (2)");
    await click(agentRow("researcher"));
    expect(container.textContent).toContain("Session Details (1)");
  });

  it("computes session share against the filtered agent total", async () => {
    await render();
    expect(sessionShareCells()).toEqual(["25%"]);
    await click(agentRow("researcher"));
    expect(sessionShareCells()).toEqual(["42%"]);
  });

  it("keeps the agent filter when the day range changes and clears via the chip", async () => {
    await render();
    await click(agentRow("coder"));
    expect(mocks.getSessionsUsage).toHaveBeenLastCalledWith(15, 0, 30, "", "coder");
    await click(button("7 Days"));
    expect(mocks.getSessionsUsage).toHaveBeenLastCalledWith(15, 0, 7, "", "coder");
    await click(button("Clear"));
    expect(mocks.getSessionsUsage).toHaveBeenLastCalledWith(15, 0, 7, "", "");
  });
});
