import { api } from "./client";

export interface FileEntry {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileEntry[];
}

export interface FileContent {
  path: string;
  content: string;
  truncated: boolean;
  message?: string;
  /** True when the file has a NUL byte or cannot be decoded; `content` is then empty. */
  binary?: boolean;
  /** Encoding that round-trips the bytes; echo it back to saveFile(). */
  encoding?: "utf-8" | "latin-1";
  /** File size in bytes. */
  size?: number;
  /** Last-modified time in ms since epoch; pass back as expectedMtimeMs on save. */
  mtime_ms?: number;
  /** Language label derived from the extension ("python", "typescript", ...). */
  language?: string;
  /** First line returned (1-indexed), when paging with startLine/endLine. */
  start_line?: number | null;
  /** Last line returned (1-indexed, inclusive). */
  end_line?: number | null;
  /** Total lines in the file (whole-file reads only). */
  total_lines?: number | null;
  /** True when more lines exist beyond end_line. */
  has_more?: boolean;
}

export interface WriteFileResult {
  status: string;
  path: string;
  size: number;
  /** New mtime in ms; store it to send as expectedMtimeMs on the next save. */
  mtime_ms?: number;
}

/** Optional request options shared by the editor endpoints. */
export interface ReadFileOptions {
  /** Chat session whose workspace to read from (falls back to the server workspace). */
  sessionId?: string;
  /** 1-indexed inclusive line range; requires the file to be read page by page. */
  startLine?: number;
  endLine?: number;
}

export interface SaveFileOptions {
  /** Chat session whose workspace to write to (falls back to the server workspace). */
  sessionId?: string;
  /** Conflict guard: reject the save with HTTP 409 when the file changed on disk. */
  expectedMtimeMs?: number;
  /** Encoding returned by readFile(); keeps non-UTF-8 bytes intact. */
  encoding?: "utf-8" | "latin-1";
}

export async function getTree(
  path: string = "",
  depth: number = 5,
  absolute: boolean = false,
  sessionId?: string,
): Promise<FileEntry[]> {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  params.set("depth", String(depth));
  if (absolute) params.set("absolute", "true");
  if (sessionId) params.set("session_id", sessionId);
  return api.get<FileEntry[]>(`/files/tree?${params}`);
}

export async function searchFiles(query: string, absolute: boolean = false, limit: number = 200, path?: string, signal?: AbortSignal): Promise<FileEntry[]> {
  const params: Record<string, string> = { q: query, limit: String(limit) };
  if (absolute) params.absolute = "true";
  if (path) params.path = path;
  const res = await api.get<{ results: FileEntry[]; total: number }>(`/files/search`, params, signal);
  return res.results;
}

export async function readFile(
  path: string,
  absolute: boolean = false,
  options: ReadFileOptions = {},
): Promise<FileContent> {
  const params = new URLSearchParams();
  params.set("path", path);
  if (absolute) params.set("absolute", "true");
  if (options.sessionId) params.set("session_id", options.sessionId);
  if (options.startLine !== undefined) params.set("start_line", String(options.startLine));
  if (options.endLine !== undefined) params.set("end_line", String(options.endLine));
  return api.get<FileContent>(`/files/read?${params}`);
}

const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif"]);

export function isImageFile(path: string): boolean {
  const ext = path.substring(path.lastIndexOf(".")).toLowerCase();
  return IMAGE_EXTENSIONS.has(ext);
}

export function getImageUrl(path: string, absolute: boolean = false): string {
  const token = localStorage.getItem("crab_token") || "";
  const params = new URLSearchParams();
  params.set("path", path);
  params.set("absolute", String(absolute));
  params.set("token", token);
  return `/api/files/image?${params}`;
}

let _activePreviewServer: { filePath: string; token: string; url: string } | null = null;

export async function startPreviewServer(filePath: string): Promise<{ token: string; url: string }> {
  if (_activePreviewServer) {
    try {
      await api.post(`/files/stop-server?token=${encodeURIComponent(_activePreviewServer.token)}`, {});
    } catch {}
  }
  const dir = filePath.substring(0, filePath.lastIndexOf("/"));
  const fileName = filePath.split("/").pop() || "index.html";
  const res = await api.post<{ token: string; url: string }>(`/files/serve-dir?path=${encodeURIComponent(dir)}`, {});
  _activePreviewServer = { filePath, token: res.token, url: `${res.url}/${encodeURIComponent(fileName)}` };
  return _activePreviewServer;
}

export async function stopPreviewServer(): Promise<void> {
  if (_activePreviewServer) {
    try {
      await api.post(`/files/stop-server?token=${encodeURIComponent(_activePreviewServer.token)}`, {});
    } catch {}
    _activePreviewServer = null;
  }
}

export interface GitChange {
  status: string;
  file: string;
}

export interface GitStatusResult {
  is_git: boolean;
  changes: GitChange[];
  diff_summary?: string;
  error?: string;
}

export interface GitDiffResult {
  is_git: boolean;
  diff: string;
  truncated?: boolean;
  error?: string;
}

export async function getGitStatus(workspace?: string): Promise<GitStatusResult> {
  const params = new URLSearchParams();
  if (workspace) params.set("workspace", workspace);
  return api.get<GitStatusResult>(`/files/git-status?${params}`);
}

export async function getGitDiff(path?: string, cached?: boolean, workspace?: string): Promise<GitDiffResult> {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  if (cached) params.set("cached", "true");
  if (workspace) params.set("workspace", workspace);
  return api.get<GitDiffResult>(`/files/git-diff?${params}`);
}

export async function saveFile(
  path: string,
  content: string,
  absolute?: boolean,
  options: SaveFileOptions = {},
): Promise<WriteFileResult> {
  const body: Record<string, unknown> = { path, content, absolute };
  if (options.sessionId) body.session_id = options.sessionId;
  if (options.expectedMtimeMs !== undefined) body.expected_mtime_ms = options.expectedMtimeMs;
  if (options.encoding) body.encoding = options.encoding;
  return api.post("/files/write", body);
}

/** Prompt payload fields describing the file the user has open in the editor. */
export interface FileContextFields {
  file_context?: string;
  file_context_dirty?: boolean;
  file_context_content?: string;
}

/**
 * Build the active-file part of a prompt request. The server injects it into
 * that one run's messages (it is never cached in the system prompt).
 */
export function fileContextFields(
  filePath?: string,
  dirty: boolean = false,
  content: string = "",
): FileContextFields {
  if (!filePath) return {};
  const fields: FileContextFields = { file_context: filePath };
  if (dirty) {
    fields.file_context_dirty = true;
    fields.file_context_content = content;
  }
  return fields;
}

// ── General file upload ────────────────────────────────────────────

export async function uploadFile(
  file: File,
): Promise<{ status: string; file: string; size: number; path: string }> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/files/upload", {
    method: "POST",
    body: formData,
    headers: api.getToken()
      ? { Authorization: `Bearer ${api.getToken()!}` }
      : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── File management: delete / rename / create / download ───────────

export async function deleteFile(path: string, absolute?: boolean): Promise<{ status: string }> {
  return api.del("/files/manage", { path, absolute });
}

export async function renameFile(oldPath: string, newPath: string, absolute?: boolean): Promise<{ status: string }> {
  return api.post("/files/rename", { old_path: oldPath, new_path: newPath, absolute });
}

export async function createEntry(path: string, entryType: "file" | "directory", absolute?: boolean): Promise<{ status: string }> {
  return api.post("/files/create", { path, entry_type: entryType, absolute });
}

export async function moveEntries(paths: string[], destination: string, absolute?: boolean): Promise<{ status: string; moved: number }> {
  return api.post("/files/move", { paths, destination, absolute });
}

export function getDownloadUrl(path: string, absolute?: boolean): string {
  const params = new URLSearchParams({ path });
  if (absolute) params.set("absolute", "true");
  const token = api.getToken();
  if (token) params.set("token", token);
  return `/api/files/download?${params.toString()}`;
}
