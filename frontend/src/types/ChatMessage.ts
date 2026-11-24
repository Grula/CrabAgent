/** The pending change of a `write`/`edit` tool call, rendered as a preview
 *  next to the confirmation card. Built server-side by
 *  `core/agent/tools/diff_preview.py`; the key is absent when there is
 *  nothing honest to show. */
export interface DiffPreview {
  /** Workspace-relative path, POSIX separators. */
  path: string;
  /** Unified diff text (no timestamps). */
  unified: string;
  added: number;
  removed: number;
  /** The body was capped; its last line reads "... [diff truncated]". */
  truncated?: boolean;
  /** The target file does not exist yet. */
  new_file?: boolean;
}

export interface ChatMessage {
  id: string;
  role: string;
  content: string;
  reasoning_content?: string;
  tool_calls?: unknown[];
  isStreaming?: boolean;
  stats?: { elapsed: number; model: string; tokens: number; iterations: number };
  confirm_id?: string;
  tool_name?: string;
  tool_call_id?: string;
  args_summary?: string;
  diff_preview?: DiffPreview;
  confirmed?: boolean;
  options?: string[];
  input_answer?: string;
  input_multiple?: boolean;
  source?: "builtin" | "mcp";
  server_name?: string;
  images?: string[];
  lazy_images?: boolean; // true when images need to be fetched separately
  db_message_id?: number; // original DB message ID for lazy image fetching
  sub_agent_id?: string;
  sub_agent_name?: string;
  sub_agent_display?: string;
  sub_agent_elapsed?: number;
  sub_agent_tokens?: number;
  sub_agent_iterations?: number;
  sub_agent_task?: string;
  sub_agent_model?: string;
  sub_agent_pipeline_run_id?: number | null;
  sub_agent_pipeline_step_id?: string | null;
  // Streaming bash output attached to a tool_call message
  bashStream?: string;
  error_info?: {
    code: string;
    title: string;
    message?: string;
    action?: string;
    retryable?: boolean;
  };
  task_card?: {
    task_id: number;
    title: string;
    deadline?: string;
    project?: string;
  };
  task_result?: {
    task_id: number;
    title: string;
    status: string;
    run_id?: number;
    result_summary?: string;
    warning_summary?: string;
    verification_status?: string;
    files?: string[];
    checks?: { passed: number; total: number };
  };
  // LLM retry status
  retry_info?: {
    phase: "retrying" | "countdown" | "exhausted";
    message: string;
    attempt: number;
    max_attempts: number;
    remaining_seconds?: number;
    delay_seconds?: number;
  };
}
