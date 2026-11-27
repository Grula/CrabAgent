/**
 * A prompt the server sent by itself: the brief an agent leaves when it hands
 * the chat to another agent (`switch_agent`), or the nudge that starts a goal's
 * next round. It is saved as a user message because the next agent must read it
 * as one, but the user never typed it, so the chat shows it as a folded note.
 * The wording comes from `serve/api/prompt.py` (`_handover_request`,
 * `_goal_continuation_request`).
 */
export interface AutoPrompt {
  /** One line for the folded note, e.g. "general handed this over". */
  title: string;
  /** What the next agent was told; shown when the note is opened. */
  body: string;
}

const HANDOVER = /^\[Handover\] (.+?) handed this request over to you:\n\n/;
const GOAL_CONTINUATION = "Continue the active goal from the latest checkpoint.";

export function autoPrompt(content: string): AutoPrompt | null {
  const handover = content.match(HANDOVER);
  if (handover) return { title: `${handover[1]} handed this over`, body: content.slice(handover[0].length) };
  if (content.startsWith(GOAL_CONTINUATION)) return { title: "Goal mode started the next round", body: content };
  return null;
}

/**
 * What the user actually typed, for a message the chat box wrapped in a
 * delegation instruction (an @agent mention becomes `[delegate_task] ...`).
 * The model needs the instruction; the chat should show the user's words.
 */
const ORIGINAL_MARKER = "\n\nOriginal request: ";

export function displayUserText(content: string): string {
  if (!content.startsWith("[delegate_task]") && !content.startsWith("[delegate_parallel]")) {
    return content;
  }
  const marker = content.indexOf(ORIGINAL_MARKER);
  if (marker >= 0) return content.slice(marker + ORIGINAL_MARKER.length);

  // Instructions sent without the original text (the delegate buttons, older messages).
  const single = content.match(/^\[delegate_task\] agent_name="([^"]+)" task="([\s\S]*)"$/);
  if (single) return `@${single[1]} ${single[2]}`;
  const parallel = content.match(/^\[delegate_parallel\] tasks=(\[[\s\S]*\])$/);
  if (parallel) {
    try {
      const tasks = JSON.parse(parallel[1]) as { agent_name: string; task: string }[];
      const agents = tasks.map((t) => `@${t.agent_name}`).join(" ");
      const sameTask = tasks.every((t) => t.task === tasks[0]?.task);
      return sameTask ? `${agents} ${tasks[0]?.task ?? ""}` : tasks.map((t) => `@${t.agent_name}: ${t.task}`).join("; ");
    } catch {
      return content;
    }
  }
  return content;
}

/**
 * A chat title for the sidebar. Older chats were titled with the raw
 * delegation instruction, often cut off mid-way ("[delegate_task]
 * agent_name="optimizer" task="Pleas..."), so this tolerates truncation.
 */
export function displayTitle(title: string): string {
  if (!title.startsWith("[delegate_task]") && !title.startsWith("[delegate_parallel]")) return title;
  const shown = displayUserText(title);
  if (shown !== title) return shown.split("\n")[0];

  const single = title.match(/^\[delegate_task\] agent_name="([^"]+)"(?: task="([^"]*))?/);
  if (single) return `@${single[1]}${single[2] ? ` ${single[2]}` : ""}`;

  const agents = Array.from(title.matchAll(/"agent_name":\s*"([^"]*)/g), (m) => m[1]).filter(Boolean);
  const task = title.match(/"task":\s*"([^"]*)/);
  if (agents.length > 0) {
    return `${Array.from(new Set(agents), (a) => `@${a}`).join(" ")}${task?.[1] ? ` ${task[1]}` : ""}`;
  }
  return "Delegated task";
}
