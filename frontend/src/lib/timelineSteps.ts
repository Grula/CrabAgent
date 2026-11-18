import type { TimelineRun, TimelineStep } from "../api/timeline";
import type { TaskInfo } from "../components/AgentRunBoard.types";

function text(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
}

/** The last few segments of a path: enough to recognise the file, short enough for one line. */
export function shortPath(path: string, segments = 3): string {
  const parts = path.split("/").filter(Boolean);
  return parts.length <= segments ? path : `…/${parts.slice(-segments).join("/")}`;
}

function lineCount(value: string): number {
  return value ? value.split("\n").length : 0;
}

/** What a step did, in one line: the command, the file, the delegated task. */
export function stepTitle(step: TimelineStep): string {
  const a = step.args;
  switch (step.tool) {
    case "bash":
      return text(a.command).split("\n")[0].replace(/^cd \S+ && /, "");
    case "read":
    case "edit":
    case "write":
      return shortPath(text(a.file_path));
    case "grep":
      return `${text(a.pattern)}${a.path ? `  in ${shortPath(text(a.path))}` : ""}`;
    case "glob":
      return text(a.pattern);
    case "delegate_task":
    case "request_help":
      return `${text(a.agent_name)}: ${text(a.task || a.question).split("\n")[0]}`;
    case "run_pipeline":
    case "delegate_parallel": {
      const items = (a.steps || a.tasks) as unknown[] | undefined;
      return `${Array.isArray(items) ? items.length : 0} steps`;
    }
    default:
      return step.summary || step.tool;
  }
}

/** "+40 −3" for an edit (lines that really changed), "+120" for a new file; empty for other tools. */
export function changeStats(step: TimelineStep): string {
  if (step.tool === "edit") {
    const lines = editDiff(step);
    return `+${lines.filter((l) => l.kind === "+").length} −${lines.filter((l) => l.kind === "-").length}`;
  }
  if (step.tool === "write") return `+${lineCount(text(step.args.content))}`;
  return "";
}

export interface DiffLine {
  kind: "+" | "-" | " ";
  text: string;
}

const DIFF_CONTEXT = 2;

/**
 * An edit as a small diff. Agents often repeat unchanged lines in both the old
 * and the new text (to anchor an insertion), so lines shared at the start and
 * end are dropped, keeping a little context around the real change.
 */
export function editDiff(step: TimelineStep): DiffLine[] {
  const oldText = text(step.args.old_string);
  const oldLines = oldText ? oldText.split("\n") : [];
  const newLines = text(step.args.new_string).split("\n");
  let start = 0;
  while (start < oldLines.length && start < newLines.length && oldLines[start] === newLines[start]) start++;
  let endOld = oldLines.length;
  let endNew = newLines.length;
  while (endOld > start && endNew > start && oldLines[endOld - 1] === newLines[endNew - 1]) {
    endOld--;
    endNew--;
  }
  const context = (lines: string[]): DiffLine[] => lines.map((line) => ({ kind: " ", text: line }));
  return [
    ...context(oldLines.slice(Math.max(0, start - DIFF_CONTEXT), start)),
    ...oldLines.slice(start, endOld).map((line): DiffLine => ({ kind: "-", text: line })),
    ...newLines.slice(start, endNew).map((line): DiffLine => ({ kind: "+", text: line })),
    ...context(oldLines.slice(endOld, endOld + DIFF_CONTEXT)),
  ];
}

export function formatClock(seconds: number): string {
  const d = new Date(seconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function formatElapsed(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s`;
}

/**
 * The recorded run behind a live card in the Agents panel. Runs recorded since
 * the card id was stored match exactly; older ones by agent and start time.
 */
export function runForTask(runs: TimelineRun[], task: TaskInfo): TimelineRun | undefined {
  const exact = runs.find((run) => run.sub_agent_id && run.sub_agent_id === task.subId);
  if (exact) return exact;
  let best: TimelineRun | undefined;
  let bestGap = 15_000;
  for (const run of runs) {
    if (run.agent !== task.agentName) continue;
    const gap = Math.abs(run.started_at * 1000 - task.startedAt);
    if (gap < bestGap) {
      best = run;
      bestGap = gap;
    }
  }
  return best;
}
