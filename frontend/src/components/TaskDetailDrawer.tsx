import { useEffect, useState, useCallback } from "react";
import {
  X as XIcon,
  Play,
  Square,
  RotateCcw,
  FileCheck2,
  ShieldCheck,
  History,
  AlertTriangle,
  Undo2,
  Package,
  ExternalLink,
} from "lucide-react";
import {
  type Task,
  type TaskDetail,
  getTaskDetail,
  startTask,
  cancelTask,
  retryTask,
  markResultViewed,
  rollbackRun,
  type RollbackResult,
} from "../api/tasks";
import { Button } from "./ui";
import { toast } from "./ui/Toast";
import { cn } from "../lib/cn";

interface Props {
  taskId: number | null;
  onClose: () => void;
  onSwitchSession?: (sessionId: string) => void;
  onTaskChanged?: () => void;
}

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  pending: { label: "Not started", color: "text-[var(--text-secondary)]" },
  in_progress: { label: "In progress", color: "text-blue-500" },
  waiting_user: { label: "Waiting on you", color: "text-orange-500" },
  done: { label: "Completed", color: "text-green-500" },
  partial: { label: "Partially done", color: "text-amber-500" },
  failed: { label: "Run failed", color: "text-red-500" },
  cancelled: { label: "Cancelled", color: "text-[var(--text-tertiary)]" },
};

const CHECK_ICONS: Record<string, string> = {
  passed: "✓",
  failed: "✗",
  warning: "△",
  pending: "○",
  skipped: "—",
};

const CHECK_COLORS: Record<string, string> = {
  passed: "text-green-500",
  failed: "text-red-500",
  warning: "text-amber-500",
  pending: "text-[var(--text-tertiary)]",
  skipped: "text-[var(--text-tertiary)]",
};

export default function TaskDetailDrawer({ taskId, onClose, onSwitchSession, onTaskChanged }: Props) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [rollbackBusy, setRollbackBusy] = useState<number | null>(null);
  const [rollbackPreview, setRollbackPreview] = useState<Record<number, RollbackResult>>({});

  const load = useCallback(async () => {
    if (!taskId) return;
    setLoading(true);
    try {
      const d = await getTaskDetail(taskId);
      setDetail(d);
      // Opening the detail drawer counts as having seen the result:
      // clears pet attention for done/partial tasks (best-effort).
      if (d.task && ["done", "partial"].includes(d.task.status) && !d.task.result_viewed_at) {
        void markResultViewed(taskId).catch(() => {});
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    load();
    setRollbackPreview({});
  }, [load]);

  const refresh = async () => {
    await load();
    onTaskChanged?.();
  };

  if (!taskId) return null;

  const task: Task | undefined = detail?.task;
  const statusInfo = task ? STATUS_LABELS[task.status] : undefined;

  const handleAction = async (action: "start" | "cancel" | "retry") => {
    if (!task) return;
    try {
      if (action === "start") await startTask(task.id);
      if (action === "cancel") await cancelTask(task.id);
      if (action === "retry") await retryTask(task.id);
      await refresh();
    } catch (e: any) {
      toast.error(e?.message || "Operation failed");
    }
  };

  const handleOpenResult = async () => {
    if (!task) return;
    try {
      await markResultViewed(task.id);
      await refresh();
    } catch {
      // ignore
    }
  };

  const handleRollback = async (runId: number, force = false) => {
    if (!task) return;
    setRollbackBusy(runId);
    try {
      const result = await rollbackRun(task.id, runId, force);
      setRollbackPreview((prev) => ({ ...prev, [runId]: result }));
      if (result.status === "conflict") {
        toast.error(`Conflict: ${result.conflicts.map((c) => c.file).join(", ")}`);
      } else if (result.restored.length > 0) {
        toast.success(`Restored ${result.restored.length} files`);
        await refresh();
      } else {
        toast.info("No file changes; rollback not performed");
      }
    } catch (e: any) {
      // 409 conflict detail carries the structured result
      const detailPayload = e?.detail;
      if (detailPayload?.conflicts) {
        setRollbackPreview((prev) => ({ ...prev, [runId]: detailPayload as RollbackResult }));
        toast.error(`Conflict: ${detailPayload.conflicts.map((c: any) => c.file).join(", ")}`);
      } else {
        toast.error(e?.message || "Operation failed");
      }
    } finally {
      setRollbackBusy(null);
    }
  };

  const primaryAction = () => {
    if (!task) return null;
    switch (task.status) {
      case "pending":
        return (
          <Button variant="brand" size="sm" onClick={() => handleAction("start")}>
            <Play size={12} className="mr-1" /> {"Start"}
          </Button>
        );
      case "in_progress":
        return (
          <Button variant="ghost" size="sm" onClick={() => handleAction("cancel")}>
            <Square size={12} className="mr-1" /> {"Stop"}
          </Button>
        );
      case "waiting_user":
        return task.source_session && onSwitchSession ? (
          <Button variant="brand" size="sm" onClick={() => onSwitchSession(task.source_session!)}>
            {"Review"}
          </Button>
        ) : null;
      case "partial":
      case "failed":
      case "cancelled":
        return (
          <Button variant="brand" size="sm" onClick={() => handleAction("retry")}>
            <RotateCcw size={12} className="mr-1" /> {"Retry"}
          </Button>
        );
      case "done":
        return (
          <Button variant="brand" size="sm" onClick={handleOpenResult}>
            <Package size={12} className="mr-1" /> {"Open result"}
          </Button>
        );
      default:
        return null;
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="w-full max-w-md h-full bg-[var(--bg-primary)] border-l border-[var(--border)] shadow-[var(--shadow-xl)] flex flex-col animate-slide-in-right"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start gap-2 px-4 py-3 border-b border-[var(--border)]">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-[var(--text-primary)] truncate">
              {task?.title || "Loading…"}
            </div>
            <div className="flex items-center gap-2 mt-1 text-xs">
              <span className={cn("font-medium", statusInfo?.color)}>
                {statusInfo?.label || task?.status}
              </span>
              {task?.owner_type === "agent" && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--accent-2-bg)] text-[var(--accent-2)]">
                  {task.owner_name || "Agent"}
                </span>
              )}
              {task?.verification_status && task.verification_status !== "unverified" && (
                <span className="text-[10px] text-[var(--text-tertiary)] flex items-center gap-0.5">
                  <ShieldCheck size={10} />
                  {task.verification_status === "passed"
                    ? "verified"
                    : task.verification_status}
                </span>
              )}
            </div>
          </div>
          {primaryAction()}
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <XIcon size={14} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {loading && !detail ? (
            <div className="text-sm text-[var(--text-tertiary)]">{"Loading…"}</div>
          ) : task ? (
            <>
              {/* Warning */}
              {task.warning_summary && (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs leading-5 text-amber-600 dark:text-amber-400">
                  <div className="flex items-center gap-1 font-medium mb-1">
                    <AlertTriangle size={12} /> {"Warnings & limits"}
                  </div>
                  <div className="whitespace-pre-wrap">{task.warning_summary}</div>
                </div>
              )}

              {/* Result */}
              {task.result_summary && (
                <section>
                  <SectionTitle icon={<FileCheck2 size={12} />} label={"Result"} />
                  <div className="text-xs text-[var(--text-secondary)] whitespace-pre-wrap leading-5 mt-1.5">
                    {task.result_summary}
                  </div>
                </section>
              )}

              {/* Artifacts */}
              <section>
                <SectionTitle icon={<Package size={12} />} label={"Artifacts"} />
                {detail?.artifacts?.length ? (
                  <div className="space-y-1.5 mt-1.5">
                    {detail.artifacts
                      .filter((a) => a.status !== "superseded")
                      .map((a) => (
                        <div
                          key={a.id}
                          className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-[var(--bg-secondary)] text-xs"
                        >
                          <span className="text-[var(--accent)]">
                            {a.action === "created" ? "+" : a.action === "modified" ? "✎" : "•"}
                          </span>
                          <span className="flex-1 truncate text-[var(--text-primary)]">{a.name}</span>
                          <span className="text-[10px] text-[var(--text-tertiary)]">v{a.version}</span>
                          {a.status === "missing" && (
                            <span className="text-[10px] text-red-500">{"missing"}</span>
                          )}
                        </div>
                      ))}
                  </div>
                ) : (
                  <EmptyHint text={"No artifacts yet"} />
                )}
              </section>

              {/* Timeline */}
              <section>
                <SectionTitle icon={<History size={12} />} label={"Timeline"} />
                {detail?.recent_events?.length ? (
                  <div className="mt-1.5 space-y-1">
                    {detail.recent_events.map((e) => (
                      <div key={e.id} className="flex items-start gap-2 text-xs">
                        <span className="text-[10px] text-[var(--text-tertiary)] shrink-0 w-12">
                          {e.created_at ? e.created_at.slice(5, 16).replace("T", " ") : ""}
                        </span>
                        <span className="text-[var(--text-secondary)] flex-1">
                          {e.title}
                          {e.detail ? (
                            <span className="block text-[10px] text-[var(--text-tertiary)] line-clamp-2">{e.detail}</span>
                          ) : null}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyHint text={"No timeline entries yet"} />
                )}
              </section>

              {/* Runs */}
              <section>
                <SectionTitle icon={<History size={12} />} label={"Run history"} />
                {detail?.runs?.length ? (
                  <div className="space-y-2 mt-1.5">
                    {detail.runs.map((run) => {
                      const preview = rollbackPreview[run.id];
                      return (
                        <div key={run.id} className="rounded-lg border border-[var(--border)] p-2.5 text-xs">
                          <div className="flex items-center gap-2">
                            <span
                              className={cn(
                                "font-medium",
                                run.status === "completed"
                                  ? "text-green-500"
                                  : run.status === "running"
                                    ? "text-blue-500 animate-pulse"
                                    : run.status === "failed" || run.status === "interrupted"
                                      ? "text-red-500"
                                      : "text-[var(--text-tertiary)]",
                              )}
                            >
                              Run #{run.id}
                            </span>
                            <span className="text-[var(--text-tertiary)]">
                              {run.agent_name} · {run.status}
                            </span>
                            {run.phase && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] truncate max-w-[120px]">
                                {run.phase}
                              </span>
                            )}
                            {run.id === task.active_run_id && (
                              <span className="text-[10px] text-blue-500">{"active"}</span>
                            )}
                          </div>
                          {run.interrupted_reason && (
                            <div className="mt-1 text-[10px] text-[var(--text-tertiary)] truncate">
                              {run.interrupted_reason}
                            </div>
                          )}
                          <div className="flex items-center gap-2 mt-1.5">
                            {run.session_id && onSwitchSession && (
                              <button
                                onClick={() => onSwitchSession(run.session_id!)}
                                className="flex items-center gap-0.5 text-[10px] text-[var(--brand)] hover:underline"
                              >
                                <ExternalLink size={10} /> {"Source session"}
                              </button>
                            )}
                            {run.status !== "running" && (
                              <button
                                onClick={() => handleRollback(run.id)}
                                disabled={rollbackBusy === run.id}
                                className="flex items-center gap-0.5 text-[10px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50"
                              >
                                <Undo2 size={10} /> {"Undo this run"}
                              </button>
                            )}
                          </div>
                          {preview && (
                            <div className="mt-1.5 text-[10px] leading-4 text-[var(--text-secondary)]">
                              {preview.status === "conflict" ? (
                                <>
                                  <div className="text-amber-500">{"Files changed after this run; overwriting is risky:"}</div>
                                  {preview.conflicts.map((c) => (
                                    <div key={c.file}>· {c.file}</div>
                                  ))}
                                  <button
                                    onClick={() => handleRollback(run.id, true)}
                                    disabled={rollbackBusy === run.id}
                                    className="mt-1 text-red-500 hover:underline"
                                  >
                                    {"Force restore anyway"}
                                  </button>
                                </>
                              ) : preview.restored.length > 0 ? (
                                <div className="text-green-500">
                                  {`Restored ${preview.restored.length} files`}
                                </div>
                              ) : (
                                <div>{"No changes to roll back"}</div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <EmptyHint text={"No runs yet"} />
                )}
              </section>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--text-secondary)]">
      {icon}
      {label}
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return <div className="text-xs text-[var(--text-tertiary)] mt-1.5">{text}</div>;
}
