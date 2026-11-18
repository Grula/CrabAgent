import { useState } from "react";
import type { TimelineStep } from "../api/timeline";
import { stepTitle } from "../lib/timelineSteps";
import { TaskInfo, TaskStatus } from "./AgentRunBoard.types";

const AGENT_ICONS: Record<string, string> = {
  researcher: "🔍", analyst: "📊", coder: "💻", writer: "📝",
};

interface Props {
  task: TaskInfo;
  onClick: () => void;
  /** This run's recorded steps, when the timeline has them. */
  steps?: TimelineStep[];
  onOpenTimeline?: () => void;
}

const CARD_STEPS = 8;

export function TaskCard({ task, onClick, steps, onOpenTimeline }: Props) {
  const [showSteps, setShowSteps] = useState(false);
  const statusConfig: Record<TaskStatus, { border: string; bg: string; badge: string; label: string }> = {
    running: { border: "var(--accent-2)", bg: "var(--accent-2-bg)", badge: "var(--accent-2)", label: "In Progress" },
    done: { border: "var(--success)", bg: "var(--success-bg)", badge: "var(--success)", label: "Completed" },
    error: { border: "var(--danger)", bg: "var(--danger-bg)", badge: "var(--danger)", label: "Failed" },
  };

  const cfg = statusConfig[task.status];
  const icon = task.icon || AGENT_ICONS[task.agentName] || "🤖";

  return (
    <div
      className="rounded-lg p-3"
      style={{
        background: cfg.bg,
        border: `1px solid ${cfg.border}30`,
        boxShadow: task.status === "running" ? `0 0 12px ${cfg.border}20` : "none",
      }}
    >
    <button onClick={onClick} className="w-full text-left transition-all hover:opacity-90">
      <div className="flex items-center gap-2.5 mb-1.5">
        <span className="text-base">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold truncate" style={{ color: "var(--text-primary)" }}>
              {task.displayName}
            </span>
            <span
              className="text-[9px] px-1.5 py-0.5 rounded-full font-medium shrink-0"
              style={{ background: `${cfg.badge}20`, color: cfg.badge }}
            >
              {task.status === "running" && (
                <span className="inline-block w-1.5 h-1.5 rounded-full mr-1 animate-pulse" style={{ background: cfg.badge }} />
              )}
              {cfg.label}
            </span>
          </div>
        </div>
      </div>

      <p className="text-[10px] leading-relaxed mb-1.5 line-clamp-2" style={{ color: "var(--text-secondary)" }}>
        {task.task}
      </p>

      <div className="flex items-center gap-3 text-[9px]" style={{ color: "var(--text-secondary)" }}>
        {task.status === "running" && (
          <>
            {task.toolCalls > 0 && <span>⚡ {task.toolCalls} tools</span>}
            <span>⏱ {Math.round((Date.now() - task.startedAt) / 1000)}s</span>
          </>
        )}
        {task.status === "done" && (
          <>
            {task.elapsed != null && <span>⏱ {task.elapsed}s</span>}
            {task.tokens != null && <span>🎫 {task.tokens}</span>}
            {task.iterations != null && <span>🔄 {task.iterations} steps</span>}
          </>
        )}
        {task.status === "error" && task.error && (
          <span className="truncate" style={{ color: "var(--danger)" }}>{task.error}</span>
        )}
      </div>
    </button>
      {steps && steps.length > 0 && (
        <div className="mt-2 pt-1.5 border-t" style={{ borderColor: `${cfg.border}30` }}>
          <button
            type="button"
            onClick={() => setShowSteps(!showSteps)}
            aria-expanded={showSteps}
            className="text-[10px] font-medium"
            style={{ color: "var(--text-secondary)" }}
          >
            {showSteps ? "▾" : "▸"} Steps ({steps.length})
          </button>
          {showSteps && (
            <ul className="mt-1 space-y-0.5">
              {steps.slice(-CARD_STEPS).map((step) => (
                <li key={step.index} className="flex items-center gap-1.5 text-[10px]" style={{ color: "var(--text-secondary)" }}>
                  <span
                    className="shrink-0"
                    style={{ color: step.status === "failed" ? "var(--danger)" : step.status === "running" ? "var(--accent-2)" : "var(--success)" }}
                  >
                    {step.status === "failed" ? "✗" : step.status === "running" ? "…" : "✓"}
                  </span>
                  <span className="shrink-0 font-medium">{step.tool}</span>
                  <span className="truncate font-mono" title={step.summary}>{stepTitle(step)}</span>
                </li>
              ))}
              {onOpenTimeline && (
                <li>
                  <button
                    type="button"
                    onClick={onOpenTimeline}
                    className="mt-1 text-[10px] underline"
                    style={{ color: "var(--accent-2)" }}
                  >
                    Open in timeline
                  </button>
                </li>
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
