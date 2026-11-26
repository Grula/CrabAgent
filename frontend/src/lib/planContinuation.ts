/** "Continue from plan file" on the start screen: the first message for a new chat. */

export interface PlanFile {
  path: string;
  title: string;
  mtime_ms: number;
}

/** The opening message that points the architect at a project's plan file. */
export function planContinuationPrompt(plan: Pick<PlanFile, "path">): string {
  return (
    `Continue the project in ${plan.path}. Read it first: it is your memory for this project ` +
    "(goal, decisions, batches, status). Then carry on with the next open batch through the usual loop, " +
    "and say which batch you are starting before you delegate."
  );
}

/** Chip label: the plan's title, or its file name without docs/ and .md. */
export function planLabel(plan: PlanFile, max = 40): string {
  const name = plan.title || plan.path.replace(/^docs\//, "").replace(/\.md$/i, "");
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}
