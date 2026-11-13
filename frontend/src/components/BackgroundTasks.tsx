import { DeliveryCard, listDeliveries, authorizeDelivery, revokeDelivery } from "../api/backgroundTasks";
import { useEffect, useRef, useState } from "react";
import { BackgroundTask, BackgroundGroup, cancelBackgroundTask, getBackgroundTask, listBackgroundTasks, listBackgroundGroups, getBackgroundGroup, cancelBackgroundGroup, SemanticSummary, getSemanticSummary, startSemanticSummary, cancelSemanticSummary, deleteSemanticSummary, getSummaryProvider, SummaryProvider } from "../api/backgroundTasks";

const labels: Record<string, string> = {
  queued: "Queued", running: "Running", succeeded: "Completed", failed: "Failed",
  cancelled: "Cancelled", cancelling: "Cancelling", interrupted: "Interrupted", skipped: "Skipped", pending: "Pending",
};

export function BackgroundTasks({ sessionId, activeBranch, mobile = false }: { sessionId: string; activeBranch?: string; mobile?: boolean }) {
  const [deliveries, setDeliveries] = useState<DeliveryCard[]>([]);
  const [open, setOpen] = useState(false);
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  const [groups, setGroups] = useState<BackgroundGroup[]>([]);
  const [groupDetail, setGroupDetail] = useState<BackgroundGroup | null>(null);
  const [detail, setDetail] = useState<BackgroundTask | null>(null);
  const [semantic, setSemantic] = useState<SemanticSummary | null>(null);
  const [provider, setProvider] = useState<SummaryProvider | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);
  const [consent, setConsent] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [deliveryNotice, setDeliveryNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const branch = useRef("");
  const generation = useRef(0);
  const requestSequence = useRef(0);

  useEffect(() => {
    let alive = true;
    generation.current++;
    branch.current = "";
    setDeliveries([]); setTasks([]); setGroups([]); setGroupDetail(null); setDetail(null); setSemantic(null); setDeleteOpen(false); setConsentOpen(false); setPassword(""); setConsent(false); setError(""); setDeliveryNotice(""); setBusy(false);
    const refresh = async () => {
      const sequence = ++requestSequence.current;
      try {
        const [data, groupData, inbox] = await Promise.all([listBackgroundTasks(sessionId), listBackgroundGroups(sessionId), listDeliveries(sessionId)]);
        if (!alive || sequence !== requestSequence.current) return;
        if (inbox.branch !== data.branch || groupData.branch !== data.branch || (activeBranch && data.branch !== activeBranch)) {
          branch.current = ""; generation.current++;
          setDeliveries([]); setTasks([]); setGroups([]); setGroupDetail(null); setDetail(null); setSemantic(null); setDeleteOpen(false); setConsentOpen(false); setPassword(""); setConsent(false); setBusy(false);
          setError("The active branch changed; waiting for it to sync.");
          return;
        }
        if (branch.current !== data.branch) {
          branch.current = data.branch;
          generation.current++;
          setDetail(null); setGroupDetail(null); setSemantic(null); setDeleteOpen(false); setConsentOpen(false); setPassword(""); setConsent(false); setBusy(false);
        }
        setTasks(data.tasks); setGroups(groupData.groups); setDeliveries(inbox.deliveries); setError("");
      } catch {
        if (alive && sequence === requestSequence.current) {
          branch.current = ""; generation.current++;
          setDeliveries([]); setTasks([]); setGroups([]); setGroupDetail(null); setDetail(null); setSemantic(null); setDeleteOpen(false); setConsentOpen(false); setPassword(""); setConsent(false); setBusy(false); setError("Could not load background tasks. Try again later.");
        }
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { alive = false; generation.current++; window.clearInterval(timer); };
  }, [sessionId, activeBranch]);

  const action = async (id: string, cancel = false) => {
    const current = generation.current;
    setBusy(true); setError("");
    try {
      const result = cancel
        ? await cancelBackgroundTask(sessionId, branch.current, id)
        : await getBackgroundTask(sessionId, branch.current, id);
      if (current !== generation.current) return;
      requestSequence.current++;
      setDetail(result);
      setTasks((items) => items.map((item) => item.id === id ? result : item));
    } catch {
      if (current === generation.current) { setDetail(null); setError("Operation failed. The task may have changed, or the session branch switched."); }
    } finally {
      if (current === generation.current) setBusy(false);
    }
  };

  const groupAction = async (id: string, cancel = false) => {
    const current = generation.current;
    setBusy(true); setError("");
    try {
      const result = cancel
        ? await cancelBackgroundGroup(sessionId, branch.current, id)
        : await getBackgroundGroup(sessionId, branch.current, id);
      if (current !== generation.current) return;
      requestSequence.current++;
      setGroupDetail(result);
      setSemantic(null); setDeleteOpen(false); setConsentOpen(false); setPassword(""); setConsent(false);
      const summary = await getSemanticSummary(sessionId, branch.current, id);
      if (current !== generation.current) return;
      setSemantic(summary.summary);
      setGroups((items) => items.map((item) => item.id === id ? result : item));
    } catch {
      if (current === generation.current) { setGroupDetail(null); setError("Operation failed. The task group may have changed, or the session branch switched."); }
    } finally {
      if (current === generation.current) setBusy(false);
    }
  };

  const summaryAction = async (operation: "refresh" | "start" | "cancel") => {
    if (!groupDetail) return;
    const current = generation.current;
    setBusy(true); setError("");
    try {
      const result = operation === "start" ? await startSemanticSummary(sessionId, branch.current, groupDetail.id, password, provider?.binding ?? "")
        : operation === "cancel" ? await cancelSemanticSummary(sessionId, branch.current, groupDetail.id)
        : await getSemanticSummary(sessionId, branch.current, groupDetail.id);
      if (current !== generation.current) return;
      setSemantic(result.summary); setConsentOpen(false); setPassword(""); setConsent(false);
    } catch {
      if (current === generation.current) { setPassword(""); setError("Semantic summary action failed. Check your password and the task status, or try again later."); }
    } finally { if (current === generation.current) setBusy(false); }
  };

  const deleteSummary = async () => {
    if (!groupDetail) return;
    const current = generation.current;
    setBusy(true); setError("");
    try {
      await deleteSemanticSummary(sessionId, branch.current, groupDetail.id);
      if (current === generation.current) { setSemantic(null); setDeleteOpen(false); }
    } catch {
      if (current === generation.current) setError("Cleanup failed. The summary may still be running, or the session branch switched.");
    } finally { if (current === generation.current) setBusy(false); }
  };

  const openConsent = async () => {
    if (!groupDetail) return;
    const current = generation.current;
    setBusy(true); setError(""); setProvider(null); setConsent(false); setPassword("");
    try {
      const identity = await getSummaryProvider(sessionId, branch.current, groupDetail.id);
      if (current === generation.current) { setProvider(identity); setConsentOpen(true); }
    } catch {
      if (current === generation.current) setError("Could not load the current provider info. Try again later.");
    } finally { if (current === generation.current) setBusy(false); }
  };

  const deliveryAction = async (id: string, authorize: boolean) => {
    if (authorize && !window.confirm("Let this task group show a standalone result card when it finishes? Results are unverified and are not added to model messages; you can revoke this at any time.")) return;
    const current = generation.current;
    setBusy(true); setError(""); setDeliveryNotice("");
    try {
      if (authorize) await authorizeDelivery(sessionId, branch.current, id);
      else await revokeDelivery(sessionId, branch.current, id);
      if (current !== generation.current) return;
      requestSequence.current++;
      if (!authorize) setDeliveries(items => items.filter(item => item.id !== id));
      setDeliveryNotice(authorize ? "This task group will show a result card when it finishes." : "Display authorization revoked.");
    } catch {
      if (current === generation.current) setError(authorize ? "Authorization failed. The task may have already finished or already be authorized." : "Revoke failed. Try again.");
    } finally {
      if (current === generation.current) setBusy(false);
    }
  };

  return <section className={mobile ? "border-b border-zinc-800 bg-zinc-950 px-4 py-2 text-sm text-zinc-100" : "border-b border-[var(--border-subtle)] bg-[var(--bg-sidebar)] px-4 py-1.5 text-xs text-[var(--text-secondary)]"}>
    <button onClick={() => setOpen(!open)} aria-expanded={open} className={mobile ? "font-medium text-zinc-100" : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors"}>
      Background subtasks ({tasks.length}) · task groups ({groups.length}) {open ? "▴" : "▾"}
    </button>
    {error && <p role="alert" className="text-[var(--danger)]">{error}</p>}
    {deliveryNotice && <p role="status">{deliveryNotice}</p>}
    {deliveries.map((card) => <article key={card.id} className="my-2 rounded border border-[var(--border)] p-3" aria-label="Background result card">
      <strong>Background result · {card.id} · {labels[card.status] ?? card.status}</strong>
      <p>Unverified data shown on its own; it is not sent to the model.</p>
      {card.steps.map((step) => <div key={step.step_id}><p>{step.step_id} · {labels[step.status] ?? step.status}</p><pre className="whitespace-pre-wrap break-words font-sans">{step.result}</pre></div>)}
      <button className="underline" disabled={busy} onClick={() => void deliveryAction(card.id, false)}>Revoke display authorization</button>
    </article>)}
    {open && <div className="mt-2 max-h-72 overflow-auto space-y-2">
      <p className={mobile ? "text-xs text-zinc-400" : "text-xs text-[var(--text-tertiary)]"}>Only the active branch of this session is shown; results are not merged into the conversation automatically.</p>
      {!error && tasks.length === 0 && groups.length === 0 && <p>No background tasks yet</p>}
      {groups.map((group) => <div key={group.id} className={mobile ? "rounded border border-zinc-700 p-2" : "rounded border border-[var(--border)] p-2"}>
        <div className="flex flex-wrap items-center gap-2">
          <strong>Task group {group.id}</strong><span>{labels[group.status] ?? group.status}</span>
          <button disabled={busy} onClick={() => void groupAction(group.id)} className="underline">View details</button>
          {(group.status === "queued" || group.status === "running") && <button disabled={busy} className="underline" onClick={() => void deliveryAction(group.id, true)}>Authorize auto-display</button>}
          <button disabled={busy} className="underline" onClick={() => void deliveryAction(group.id, false)}>Revoke display authorization</button>
          {(group.status === "queued" || group.status === "running") && <button disabled={busy} onClick={() => void groupAction(group.id, true)} className="underline">Cancel task group</button>}
        </div>
        {groupDetail?.id === group.id && <div className="mt-2 space-y-2">
          {groupDetail.steps?.map((step) => <div key={step.step_id} className="break-words">
            <p><strong>{step.step_id}</strong> · {step.agent_name} · {labels[step.status] ?? step.status}</p>
            <p>{step.task}</p><p>Dependencies: {step.depends_on.join(", ") || "None"}</p>
            <p>Subtask output (unverified)</p><pre className="whitespace-pre-wrap break-words font-sans">{step.result || "No results yet"}</pre>
          </div>)}
          <div className="border-t border-current pt-2 space-y-2">
            <p>Semantic summary (model-generated, unverified){semantic && ` · ${labels[semantic.status] ?? semantic.status}`}</p>
            {semantic && <pre className="whitespace-pre-wrap break-words font-sans">{semantic.text || "No results yet"}</pre>}
            <button disabled={busy} onClick={() => void summaryAction("refresh")} className="underline">Refresh semantic summary status</button>{" "}
            {semantic && ["pending", "running", "cancelling"].includes(semantic.status) && <button disabled={busy} onClick={() => void summaryAction("cancel")} className="underline">Cancel semantic summary</button>}
            {semantic && ["succeeded", "failed", "interrupted", "cancelled"].includes(semantic.status) && !deleteOpen && <button disabled={busy} onClick={() => setDeleteOpen(true)} className="underline">Delete local semantic summary</button>}
            {deleteOpen && <div className="space-y-2">
              <p>Delete the local semantic summary and its step snapshots? This cannot be undone. It does not delete the original task results, database backups or data stored by the provider, and it does not guarantee erasure from the storage medium.</p>
              <button disabled={busy} onClick={() => void deleteSummary()} className="underline">Confirm cleanup</button>{" "}
              <button disabled={busy} onClick={() => setDeleteOpen(false)} className="underline">Cancel cleanup</button>
            </div>}
            {(!semantic || ["failed", "interrupted", "cancelled"].includes(semantic.status)) && ["succeeded", "failed"].includes(group.status) && !consentOpen && <button disabled={busy} onClick={() => void openConsent()} className="underline">Request semantic summary</button>}
            {consentOpen && <div className="space-y-2">
              <p>The status and results of every step in this task group will be sent to the model provider configured for this session and may contain sensitive information; the provider may store or process that data. Step-result snapshots and the summary text are also stored in plain text in the local database with no automatic expiry, and can be deleted manually once the summary finishes. Cancelling does not guarantee the remote side stops. Check the step results first.</p>
              <p>Provider: {provider?.name} ({provider?.type}); Model: {provider?.model}; Endpoint: {provider?.endpoint}; Proxy: {provider?.proxy}</p>
              <p>The summary request uses exactly the same provider, model and network path as your normal conversations (the proxy follows the global config); you must confirm again after the provider or model config changes.</p>
              <label><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> I explicitly consent to sending the data above to generate a semantic summary</label>
              <label className="block">Enter your account password to confirm <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className="text-black" /></label>
              <button disabled={busy || !consent || !password || !provider} onClick={() => void summaryAction("start")} className="underline">Confirm and generate</button>{" "}
              <button onClick={() => { setConsentOpen(false); setPassword(""); setConsent(false); }} className="underline">Back</button>
            </div>}
          </div>
          {groupDetail.summary && <div><p>Summary (unverified) · {labels[groupDetail.summary.status] ?? groupDetail.summary.status}</p><pre className="whitespace-pre-wrap break-words font-sans">{groupDetail.summary.text || "No results yet"}</pre></div>}
        </div>}
      </div>)}
      {tasks.map((task) => <div key={task.id} className={mobile ? "rounded border border-zinc-700 p-2" : "rounded border border-[var(--border)] p-2"}>
        <div className="flex flex-wrap items-center gap-2">
          <strong>{task.agent_name}</strong><span>{labels[task.status] ?? task.status}</span>
          <button disabled={busy} onClick={() => void action(task.id)} className="underline">View results</button>
          {(task.status === "queued" || task.status === "running") && <button disabled={busy} onClick={() => void action(task.id, true)} className="underline">Cancel task</button>}
        </div>
        <p className={mobile ? "break-words text-zinc-300" : "break-words text-[var(--text-secondary)]"}>{task.task}</p>
        {detail?.id === task.id && <div className="mt-2"><p className={mobile ? "text-xs text-zinc-400" : "text-xs text-[var(--text-tertiary)]"}>Subtask output (unverified)</p><pre className="whitespace-pre-wrap break-words font-sans">{detail.result || "No results yet"}</pre></div>}
      </div>)}
    </div>}
  </section>;
}
