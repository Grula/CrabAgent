import { useEffect, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Clock, Layers, Zap } from "lucide-react";
import { Tooltip } from "./ui";
import { cn } from "../lib/cn";
import {
  getSessionUsageSummary,
  type SessionUsageSummary,
} from "../api/tokenUsage";

interface Props {
  sessionId: string | null;
  /** Changing this refetches (e.g. messages.length, sending state) */
  refreshKey?: number;
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtElapsed(sec: number): string {
  if (sec >= 3600) return `${(sec / 3600).toFixed(1)}h`;
  if (sec >= 60) return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`;
  return `${sec.toFixed(sec < 10 ? 1 : 0)}s`;
}

function Stat({
  icon,
  label,
  value,
  sub,
  subClass,
  detail,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  sub?: string;
  subClass?: string;
  detail?: string;
}) {
  return (
    <Tooltip
      content={
        <span>
          <span className="text-[var(--text-primary)] font-medium">{detail || label}</span>
        </span>
      }
      side="bottom"
    >
      <span className="flex items-center gap-1 shrink-0">
        <span className="text-[var(--text-tertiary)]">{icon}</span>
        <span className="font-medium text-[var(--text-secondary)]">{value}</span>
        {sub && <span className={cn("text-[var(--text-tertiary)]", subClass)}>{sub}</span>}
      </span>
    </Tooltip>
  );
}

/**
 * Session-level usage bar: total tokens / input (cached·uncached) / output / accumulated duration / call count.
 * Data comes from /token-usage/session/{id}/summary and refreshes debounced as refreshKey changes.
 */
export function SessionStatsBar({ sessionId, refreshKey = 0 }: Props) {
  const [summary, setSummary] = useState<SessionUsageSummary | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setSummary(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      getSessionUsageSummary(sessionId)
        .then((data) => {
          if (!cancelled) setSummary(data);
        })
        .catch(() => {});
    }, 600);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sessionId, refreshKey]);

  if (!summary || summary.calls === 0) return null;

  const cacheRate = Math.round(summary.cache_hit_rate * 100);
  const cacheGood = summary.cache_hit_rate >= 0.7;

  return (
    <div className="flex items-center gap-3 overflow-x-auto pb-1.5 mb-1.5 scrollbar-none text-[11px]">
      <Stat
        icon={<Zap size={12} />}
        label="Total tokens"
        value={fmtTokens(summary.total_tokens)}
        detail={`Total ${summary.total_tokens.toLocaleString()} tokens`}
      />
      <Stat
        icon={<ArrowUp size={12} className="text-[var(--accent)]" />}
        label="Input tokens"
        value={fmtTokens(summary.prompt_tokens)}
        sub={`Cache ${fmtTokens(summary.cached_tokens)} · ${cacheRate}%`}
        subClass={cacheGood ? "text-[var(--success)]" : undefined}
        detail={`Input ${summary.prompt_tokens.toLocaleString()} tokens (cache hit ${summary.cached_tokens.toLocaleString()}, non-cached ${summary.non_cached_tokens.toLocaleString()}, hit rate ${cacheRate}%)`}
      />
      <Stat
        icon={<ArrowDown size={12} className="text-[var(--warning)]" />}
        label="Output tokens"
        value={fmtTokens(summary.completion_tokens)}
        detail={`Output ${summary.completion_tokens.toLocaleString()} tokens${
          summary.reasoning_tokens > 0
            ? ` (incl. reasoning ${summary.reasoning_tokens.toLocaleString()})`
            : ""
        }`}
      />
      <Stat
        icon={<Clock size={12} />}
        label="Total time"
        value={fmtElapsed(summary.elapsed)}
        detail={`Total ${summary.elapsed.toLocaleString()}s · ${summary.turns} turns`}
      />
      <Stat
        icon={<Layers size={12} />}
        label="Calls"
        value={`${summary.calls}`}
        detail={`${summary.calls} LLM calls`}
      />
    </div>
  );
}
