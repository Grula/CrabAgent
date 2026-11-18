import { useEffect, useState } from "react";
import * as goalsApi from "../api/goals";
import { formatTime, parseDateTime } from "../api/time";
import { Goal } from "../types/Goal";
import { Modal } from "./ui";

/** Local time of day for a UTC wire timestamp; "" when unusable. */
function localTimeOfDay(iso: string): string {
  const d = parseDateTime(iso);
  return d ? d.toLocaleTimeString() : "";
}

interface Props {
  open: boolean;
  sessionId: string;
  goal: Goal | null;
  onOpenChange: (open: boolean) => void;
}

export function GoalTimeline({ open, sessionId, goal, onOpenChange }: Props) {
  const [events, setEvents] = useState<goalsApi.GoalHistoryEvent[]>([]);
  const [checkpoints, setCheckpoints] = useState<goalsApi.GoalCheckpoint[]>([]);

  useEffect(() => {
    if (!open || !goal) return;
    goalsApi.getGoalHistory(sessionId).then((data) => {
      setEvents(data.events);
      setCheckpoints(data.checkpoints);
    }).catch(() => {
      setEvents([]);
      setCheckpoints([]);
    });
  }, [open, sessionId, goal?.id]);

  return <Modal open={open} onOpenChange={onOpenChange} title="Goal progress" description={goal?.objective || ""} size="lg">
    <div className="space-y-5">
      {goal?.completion_evidence && <section className="rounded-xl border border-emerald-500/25 bg-emerald-500/8 p-3">
        <p className="text-xs font-medium text-emerald-700 dark:text-emerald-300">Completion evidence</p>
        <p className="mt-1 whitespace-pre-wrap text-sm text-[var(--text-primary)]">{goal.completion_evidence}</p>
      </section>}
      {goal?.blocker && <section className="rounded-xl border border-red-500/25 bg-red-500/8 p-3">
        <p className="text-xs font-medium text-red-700 dark:text-red-300">Blocking reason</p>
        <p className="mt-1 whitespace-pre-wrap text-sm text-[var(--text-primary)]">{goal.blocker}</p>
      </section>}
      <section>
        <h3 className="text-xs font-semibold text-[var(--text-secondary)]">Checkpoint</h3>
        <div className="mt-2 space-y-3 border-l border-[var(--border)] pl-4">
          {checkpoints.length === 0 && <p className="text-sm text-[var(--text-tertiary)]">No checkpoints yet.</p>}
          {checkpoints.map((checkpoint) => <article key={checkpoint.id} className="relative">
            <i className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-[var(--brand)]" />
            <p className="text-sm text-[var(--text-primary)]">{checkpoint.summary}</p>
            {checkpoint.next_step && <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">Next step: {checkpoint.next_step}</p>}
            <time className="mt-1 block text-[11px] text-[var(--text-tertiary)]">{formatTime(checkpoint.created_at)}</time>
          </article>)}
        </div>
      </section>
      <section>
        <h3 className="text-xs font-semibold text-[var(--text-secondary)]">Status log</h3>
        <div className="mt-2 space-y-2">
          {events.map((event, index) => <div key={`${event.created_at}-${index}`} className="flex gap-3 text-xs">
            <time className="shrink-0 text-[var(--text-tertiary)]">{localTimeOfDay(event.created_at)}</time>
            <p className="text-[var(--text-secondary)]">{event.detail}</p>
          </div>)}
        </div>
      </section>
    </div>
  </Modal>;
}
