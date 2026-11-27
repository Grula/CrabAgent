import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, Check, ChevronDown, ChevronRight, FilePen, FilePlus, FileText, FolderSearch, Loader2,
  Search, Terminal, Users, Workflow, Wrench, X,
} from "lucide-react";
import type { SessionTimeline, TimelineStep } from "../api/timeline";
import { changeStats, editDiff, formatClock, formatElapsed, stepTitle } from "../lib/timelineSteps";
import { agentColorVar } from "./SubAgentCard";
import { cn } from "../lib/cn";

const TOOL_ICONS: Record<string, typeof Terminal> = {
  bash: Terminal, edit: FilePen, write: FilePlus, read: FileText, grep: Search, glob: FolderSearch,
  delegate_task: Users, request_help: Users, delegate_parallel: Workflow, run_pipeline: Workflow,
};

interface Props {
  timeline: SessionTimeline | null;
  error?: string;
  hasSession: boolean;
  /** Agent name to show, or "" for everyone. */
  agentFilter: string;
  onAgentFilterChange: (agent: string) => void;
}

export function AgentTimeline({ timeline, error, hasSession, agentFilter, onAgentFilterChange }: Props) {
  const listRef = useRef<HTMLDivElement>(null);
  const stickToTop = useRef(true);

  const agents = useMemo(() => {
    const seen = new Map<string, { name: string; label: string; icon: string; count: number }>();
    for (const run of timeline?.runs ?? []) {
      if (!seen.has(run.agent)) seen.set(run.agent, { name: run.agent, label: run.display_name, icon: run.icon, count: 0 });
    }
    for (const step of timeline?.steps ?? []) {
      const entry = seen.get(step.agent);
      if (entry) entry.count += 1;
    }
    return [...seen.values()];
  }, [timeline]);
  const labels = useMemo(() => new Map(agents.map((a) => [a.name, a])), [agents]);
  // Newest first: the API sends steps oldest first.
  const steps = useMemo(
    () => (timeline?.steps ?? []).filter((s) => !agentFilter || s.agent === agentFilter).reverse(),
    [timeline, agentFilter],
  );

  // New steps arrive on top: stay at the top to see them. Someone who scrolled down to
  // read keeps their place (the browser's scroll anchoring holds the rows in view).
  useEffect(() => {
    const el = listRef.current;
    if (el && stickToTop.current) el.scrollTop = 0;
  }, [steps.length]);

  if (!hasSession) return <Empty text="Open a chat to see what its agents do." />;
  if (!timeline) return <Empty text={error ? `Could not load the timeline: ${error}` : "Loading the timeline…"} />;

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-[var(--border)] overflow-x-auto shrink-0">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-[var(--text-primary)] mr-2 shrink-0">
          <Activity size={13} />
          Timeline
          {timeline.running && (
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full animate-pulse bg-[var(--accent-2-bg)] text-[var(--accent-2)]">
              live
            </span>
          )}
        </span>
        <FilterChip active={!agentFilter} onClick={() => onAgentFilterChange("")} label="All" count={timeline.steps.length} />
        {agents.map((a) => (
          <FilterChip
            key={a.name}
            active={agentFilter === a.name}
            onClick={() => onAgentFilterChange(agentFilter === a.name ? "" : a.name)}
            label={`${a.icon ? `${a.icon} ` : ""}${a.label}`}
            count={a.count}
          />
        ))}
      </div>
      <div
        ref={listRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToTop.current = el.scrollTop < 40;
        }}
        className="flex-1 min-h-0 overflow-y-auto py-1"
      >
        {steps.length === 0 ? (
          <Empty text={timeline.steps.length ? "This agent has no steps yet." : "No agent activity in this chat yet."} />
        ) : (
          steps.map((step) => (
            <StepRow key={`${step.run_id}-${step.index}`} step={step} agentLabel={labels.get(step.agent)?.label ?? step.agent} />
          ))
        )}
      </div>
    </div>
  );
}

function FilterChip({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "shrink-0 text-[11px] px-2 py-0.5 rounded-full border transition-colors",
        active
          ? "border-[var(--accent)] bg-[var(--bg-tertiary)] text-[var(--text-primary)]"
          : "border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
      )}
    >
      {label} <span className="text-[var(--text-tertiary)]">{count}</span>
    </button>
  );
}

function StepRow({ step, agentLabel }: { step: TimelineStep; agentLabel: string }) {
  const [open, setOpen] = useState(false);
  const Icon = TOOL_ICONS[step.tool] ?? Wrench;
  const stats = changeStats(step);
  return (
    <div className="border-b border-[var(--border)]/40">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-[var(--bg-tertiary)] text-[12px]"
      >
        {open ? <ChevronDown size={12} className="shrink-0 text-[var(--text-tertiary)]" /> : <ChevronRight size={12} className="shrink-0 text-[var(--text-tertiary)]" />}
        <span className="shrink-0 tabular-nums text-[11px] text-[var(--text-tertiary)]">{formatClock(step.started_at)}</span>
        <span
          className="shrink-0 w-28 truncate text-[11px] font-medium"
          style={{ color: agentColorVar(step.agent) ?? "var(--text-secondary)" }}
          title={agentLabel}
        >
          {agentLabel}
        </span>
        <span className="shrink-0 flex items-center gap-1 w-24 text-[11px] text-[var(--text-secondary)]">
          <Icon size={12} />
          {step.tool}
        </span>
        <span className={cn("flex-1 min-w-0 truncate", step.tool === "bash" && "font-mono text-[11px]")} title={step.summary}>
          {stepTitle(step)}
        </span>
        {stats && <span className="shrink-0 font-mono text-[10px] text-[var(--text-tertiary)]">{stats}</span>}
        <StatusMark status={step.status} />
        <span className="shrink-0 w-14 text-right tabular-nums text-[10px] text-[var(--text-tertiary)]">
          {step.status === "running" ? "" : formatElapsed(step.elapsed)}
        </span>
      </button>
      {open && <StepDetail step={step} />}
    </div>
  );
}

function StatusMark({ status }: { status: TimelineStep["status"] }) {
  if (status === "running") return <Loader2 size={12} className="shrink-0 animate-spin text-[var(--accent-2)]" aria-label="running" />;
  if (status === "failed") return <X size={12} className="shrink-0 text-[var(--danger)]" aria-label="failed" />;
  return <Check size={12} className="shrink-0 text-[var(--success)]" aria-label="ok" />;
}

function StepDetail({ step }: { step: TimelineStep }) {
  const a = step.args;
  const str = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));
  return (
    <div className="px-9 pb-3 pt-1 space-y-2 text-[11px]">
      {step.summary && <div className="text-[var(--text-secondary)] italic">{step.summary}</div>}
      {step.tool === "bash" && <Block label="command" body={str(a.command)} />}
      {(step.tool === "edit" || step.tool === "write" || step.tool === "read") && (
        <div className="font-mono text-[var(--text-secondary)] break-all">{str(a.file_path)}</div>
      )}
      {step.tool === "edit" && (
        <pre className="max-h-96 overflow-auto rounded-md border border-[var(--border)] bg-[var(--bg-secondary)] p-2 font-mono text-[11px] leading-snug">
          {editDiff(step).map((line, i) => (
            <div key={i} style={{ color: line.kind === "+" ? "var(--success)" : line.kind === "-" ? "var(--danger)" : "var(--text-tertiary)" }}>
              {line.kind} {line.text}
            </div>
          ))}
        </pre>
      )}
      {step.tool === "write" && <Block label="content" body={str(a.content)} />}
      {!["bash", "edit", "write", "read"].includes(step.tool) && Object.keys(a).length > 0 && (
        <Block label="arguments" body={str(a)} />
      )}
      {step.result && <Block label={step.status === "failed" ? "output (failed)" : "output"} body={step.result} />}
    </div>
  );
}

function Block({ label, body }: { label: string; body: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-[var(--text-tertiary)] mb-0.5">{label}</div>
      <pre className="max-h-72 overflow-auto rounded-md border border-[var(--border)] bg-[var(--bg-secondary)] p-2 font-mono text-[11px] leading-snug whitespace-pre-wrap break-words">
        {body}
      </pre>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="h-full flex items-center justify-center p-6 text-sm text-[var(--text-tertiary)]">{text}</div>;
}
