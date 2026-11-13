import { useEffect, useState } from "react";
import { Button, Modal, Textarea } from "./ui";
import { Goal, GoalDraft } from "../types/Goal";

interface Props {
  open: boolean;
  goal: Goal | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (draft: GoalDraft) => Promise<void>;
}

const blank: GoalDraft = { objective: "", success_criteria: [], constraints: [], auto_continue: false };
const lines = (value: string) => value.split("\n").map((line) => line.trim()).filter(Boolean);

export function GoalComposer({ open, goal, onOpenChange, onSubmit }: Props) {
  const [draft, setDraft] = useState<GoalDraft>(blank);
  const [criteriaText, setCriteriaText] = useState("");
  const [constraintsText, setConstraintsText] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(goal ? { objective: goal.objective, success_criteria: goal.success_criteria, constraints: goal.constraints, auto_continue: goal.auto_continue, token_budget: goal.token_budget, max_auto_turns: goal.max_auto_turns } : blank);
    setCriteriaText(goal?.success_criteria.join("\n") || "");
    setConstraintsText(goal?.constraints.join("\n") || "");
  }, [goal, open]);

  const submit = async () => {
    if (!draft.objective.trim()) return;
    setSaving(true);
    try {
      await onSubmit({ ...draft, objective: draft.objective.trim(), success_criteria: lines(criteriaText), constraints: lines(constraintsText) });
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return <Modal open={open} onOpenChange={onOpenChange} title={goal ? "Edit goal" : "Create Goal"} description="The goal stays visible in this session; chat output remains in the original message flow." size="lg" footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="brand" onClick={submit} disabled={!draft.objective.trim() || saving}>{saving ? "Saving..." : goal ? "Save goal" : "Start goal"}</Button></>}>
    <div className="space-y-4">
      <label className="block text-xs font-medium text-[var(--text-secondary)]">Goal
        <Textarea value={draft.objective} onChange={(e) => setDraft({ ...draft, objective: e.target.value })} placeholder="e.g. finish the pet-system failure retry and make sure the related tests pass" className="mt-1.5" minRows={3} maxRows={6} />
      </label>
      <label className="block text-xs font-medium text-[var(--text-secondary)]">Acceptance criteria <span className="font-normal text-[var(--text-tertiary)]">One item per line, optional</span>
        <Textarea value={criteriaText} onChange={(e) => setCriteriaText(e.target.value)} placeholder={"pytest tests/test_pets.py passes\nElectron IPC returns retry status"} className="mt-1.5" minRows={3} maxRows={6} />
      </label>
      <label className="block text-xs font-medium text-[var(--text-secondary)]">Constraints <span className="font-normal text-[var(--text-tertiary)]">One item per line, optional</span>
        <Textarea value={constraintsText} onChange={(e) => setConstraintsText(e.target.value)} placeholder="Do not change the existing pet-settings interaction" className="mt-1.5" minRows={2} maxRows={5} />
      </label>
      <label className="flex items-center justify-between gap-3 rounded-xl border border-[var(--border)] px-3 py-2.5 text-sm text-[var(--text-primary)]">
        <span><span className="block font-medium">Auto-continue</span><span className="block mt-0.5 text-xs text-[var(--text-tertiary)]">Continue after the current turn until done, paused, or the budget is reached.</span></span>
        <input type="checkbox" checked={draft.auto_continue} onChange={(e) => setDraft({ ...draft, auto_continue: e.target.checked })} className="h-4 w-4 accent-[var(--brand)]" />
      </label>
    </div>
  </Modal>;
}
