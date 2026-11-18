import { api } from "./client";

export interface AgentMonitorInfo {
  session_id: string;
  model: string;
  status: string;
  started_at: number;
  elapsed: number;
  workspace?: string;
  title?: string;
}

export async function getAgentMonitor(signal?: AbortSignal): Promise<AgentMonitorInfo[]> {
  return api.get<AgentMonitorInfo[]>("/agents/monitor", undefined, signal);
}

export interface GlobalSSEEvent {
  type: string;
  data: Record<string, unknown>;
  timestamp: number;
}

export function connectGlobalSSE(onEvent: (event: GlobalSSEEvent) => void): EventSource | null {
  const token = localStorage.getItem("crab_token");
  if (!token) return null; // Not authenticated — don't hammer the API with 401s
  const url = `/api/events/global?token=${encodeURIComponent(token)}`;
  const es = new EventSource(url);
  es.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data) as GlobalSSEEvent;
      onEvent(event);
    } catch {
      // ignore non-JSON (keepalive, etc.)
    }
  };
  return es;
}
