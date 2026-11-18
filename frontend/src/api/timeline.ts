import { api } from "./client";

export type TimelineStepStatus = "running" | "ok" | "failed";

/** One agent run in a chat (the chat's own agent, or a delegated teammate). */
export interface TimelineRun {
  run_id: number;
  parent_run_id: number | null;
  agent: string;
  display_name: string;
  icon: string;
  status: string;
  task: string;
  started_at: number;
  finished_at: number | null;
  elapsed: number;
  step_count: number;
  error: string;
  /** Matches the live card in the Agents panel; empty for older runs. */
  sub_agent_id: string;
}

/** One tool call: a command run, a file read or changed, a delegation. */
export interface TimelineStep {
  run_id: number;
  agent: string;
  index: number;
  tool: string;
  /** The agent's own one-line label for the call. */
  summary: string;
  args: Record<string, unknown>;
  /** Unix seconds. */
  started_at: number;
  elapsed: number;
  status: TimelineStepStatus;
  /** The first 500 characters of the tool's output. */
  result: string;
}

export interface SessionTimeline {
  session_id: string;
  running: boolean;
  runs: TimelineRun[];
  steps: TimelineStep[];
}

export function getTimeline(sessionId: string): Promise<SessionTimeline> {
  return api.get(`/sessions/${encodeURIComponent(sessionId)}/timeline`);
}
