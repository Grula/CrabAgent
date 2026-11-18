// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SessionTimeline, TimelineStep } from "../api/timeline";
import { changeStats, editDiff, runForTask, stepTitle } from "../lib/timelineSteps";
import { AgentTimeline } from "./AgentTimeline";
import { TaskCard } from "./AgentRunCard";
import type { TaskInfo } from "./AgentRunBoard.types";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

function step(partial: Partial<TimelineStep>): TimelineStep {
  return { run_id: 1, agent: "coder", index: 0, tool: "bash", summary: "", args: {}, started_at: 1_700_000_000,
    elapsed: 0.4, status: "ok", result: "", ...partial };
}

const timeline: SessionTimeline = {
  session_id: "s1",
  running: true,
  runs: [
    { run_id: 1, parent_run_id: null, agent: "tester", display_name: "QA Engineer", icon: "🧪", status: "completed",
      task: "tests", started_at: 1_700_000_000, finished_at: 1_700_000_050, elapsed: 50, step_count: 1, error: "", sub_agent_id: "sub-t" },
    { run_id: 2, parent_run_id: null, agent: "coder", display_name: "Senior Developer", icon: "💻", status: "running",
      task: "code", started_at: 1_700_000_060, finished_at: null, elapsed: 0, step_count: 2, error: "", sub_agent_id: "" },
  ],
  steps: [
    step({ run_id: 1, agent: "tester", tool: "bash", args: { command: "cd /repo && python3 -m pytest -q" }, result: "1 failed" , status: "failed" }),
    step({ run_id: 2, agent: "coder", index: 0, tool: "edit", started_at: 1_700_000_070,
      args: { file_path: "/home/x/projects/splitbill/splitbill/ledger.py", old_string: "a = 1", new_string: "a = 2\nb = 3" } }),
    step({ run_id: 2, agent: "coder", index: 1, tool: "bash", started_at: 1_700_000_080, status: "running",
      args: { command: "python3 -m pytest -q" } }),
  ],
};

let root: Root | null = null;
function render(node: React.ReactNode) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(node));
  return host;
}
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
});

describe("timeline helpers", () => {
  it("describes a step in one line", () => {
    expect(stepTitle(timeline.steps[0])).toBe("python3 -m pytest -q"); // the cd prefix is noise
    expect(stepTitle(timeline.steps[1])).toBe("…/splitbill/splitbill/ledger.py"); // last 3 path segments
    expect(changeStats(timeline.steps[1])).toBe("+2 −1");
    expect(editDiff(timeline.steps[1]).map((l) => `${l.kind}${l.text}`)).toEqual(["-a = 1", "+a = 2", "+b = 3"]);
  });

  it("shows only the real change when an edit repeats its anchor lines", () => {
    const append = step({ tool: "edit", args: { old_string: "def a():\n    pass", new_string: "def a():\n    pass\n\ndef b():\n    pass" } });
    expect(editDiff(append).map((l) => `${l.kind}${l.text}`)).toEqual([" def a():", "     pass", "+", "+def b():", "+    pass"]);
    expect(changeStats(append)).toBe("+3 −0");
  });

  it("matches a live card to its recorded run, exactly or by agent and start time", () => {
    const card = (subId: string, agentName: string, startedAt: number) => ({ subId, agentName, startedAt }) as TaskInfo;
    expect(runForTask(timeline.runs, card("sub-t", "anything", 0))?.run_id).toBe(1);
    expect(runForTask(timeline.runs, card("other", "coder", 1_700_000_062_000))?.run_id).toBe(2);
    expect(runForTask(timeline.runs, card("other", "coder", 1_700_009_000_000))).toBeUndefined();
  });
});

describe("AgentTimeline", () => {
  it("lists every agent's steps and filters by agent", () => {
    const onFilter = vi.fn();
    const host = render(<AgentTimeline timeline={timeline} hasSession agentFilter="" onAgentFilterChange={onFilter} />);
    expect(host.textContent).toContain("python3 -m pytest -q");
    expect(host.textContent).toContain("ledger.py");
    expect(host.querySelector('[aria-label="running"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="failed"]')).not.toBeNull();

    const coderChip = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Senior Developer"))!;
    act(() => coderChip.click());
    expect(onFilter).toHaveBeenCalledWith("coder");
  });

  it("shows only the chosen agent's steps", () => {
    const host = render(<AgentTimeline timeline={timeline} hasSession agentFilter="tester" onAgentFilterChange={() => {}} />);
    expect(host.textContent).toContain("pytest");
    expect(host.textContent).not.toContain("ledger.py");
  });

  it("expands an edit into its diff and a command into its output", () => {
    const host = render(<AgentTimeline timeline={timeline} hasSession agentFilter="" onAgentFilterChange={() => {}} />);
    const rows = [...host.querySelectorAll('button[aria-expanded]')];
    act(() => (rows[1] as HTMLButtonElement).click());
    expect(host.textContent).toContain("- a = 1");
    expect(host.textContent).toContain("+ b = 3");
    act(() => (rows[0] as HTMLButtonElement).click());
    expect(host.textContent).toContain("1 failed");
  });

  it("says why it is empty", () => {
    expect(render(<AgentTimeline timeline={null} hasSession={false} agentFilter="" onAgentFilterChange={() => {}} />).textContent)
      .toContain("Open a chat");
  });
});

describe("agent card steps", () => {
  it("lists the run's steps and opens the timeline", () => {
    const onOpen = vi.fn();
    const task: TaskInfo = { subId: "sub-t", agentName: "coder", displayName: "Senior Developer", icon: "💻", status: "running",
      task: "code", content: "", toolCalls: 2, startedAt: Date.now() };
    const host = render(<TaskCard task={task} onClick={() => {}} steps={timeline.steps.slice(1)} onOpenTimeline={onOpen} />);
    const toggle = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Steps (2)"))!;
    act(() => toggle.click());
    expect(host.textContent).toContain("ledger.py");
    const open = [...host.querySelectorAll("button")].find((b) => b.textContent === "Open in timeline")!;
    act(() => open.click());
    expect(onOpen).toHaveBeenCalled();
  });
});
