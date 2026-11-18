import { api, ApiError } from "./client";
import type { OfficeCliStatus } from "./officecli";

export interface DocPreviewResponse {
  html: string;
  file: string;
  name: string;
}

export function getDownloadUrl(path: string): string {
  const params = new URLSearchParams({ path });
  const token = api.getToken();
  if (token) params.set("token", token);
  return `/api/documents/download?${params.toString()}`;
}

export async function getPreview(
  path: string,
  workspace = "",
  onInstallProgress?: (status: OfficeCliStatus) => void,
): Promise<DocPreviewResponse> {
  const params: Record<string, string> = { path };
  if (workspace) params.workspace = workspace;
  try {
    return await api.get("/documents/preview", params);
  } catch (e: any) {
    if (e instanceof ApiError && e.status === 503 && e.data?.installing) {
      // OfficeCLI is being installed — poll status and retry
      await _waitForInstall(onInstallProgress);
      return api.get("/documents/preview", params);
    }
    throw e;
  }
}

async function _waitForInstall(
  onProgress?: (status: OfficeCliStatus) => void,
): Promise<void> {
  const { getOfficeCliStatus } = await import("./officecli");
  return new Promise((resolve) => {
    const poll = async () => {
      try {
        const status = await getOfficeCliStatus();
        onProgress?.(status);
        if (status.status === "ready" || status.available) {
          resolve();
          return;
        }
        if (status.status === "failed") {
          resolve(); // let the caller handle the retry error
          return;
        }
      } catch {
        // ignore poll errors
      }
      setTimeout(poll, 2000);
    };
    // Start polling after 1s delay
    setTimeout(poll, 1000);
  });
}

export interface QuickEditTextRequest {
  path: string;
  old_text: string;
  new_text: string;
  workspace?: string;
}

export interface QuickEditTextResponse {
  status: string;
  preview_html: string | null;
  message: string;
}

export function quickEditText(
  req: QuickEditTextRequest,
): Promise<QuickEditTextResponse> {
  return api.post("/documents/quick-edit/text", req);
}

// ── Quick Edit: Style ─────────────────────────────────────────────

export interface StyleChange {
  element: string;
  props: Record<string, string | number | boolean>;
}

export interface QuickEditStyleRequest {
  path: string;
  workspace?: string;
  changes: StyleChange[];
}

export interface QuickEditStyleResponse {
  status: string;
  preview_html: string | null;
  results: Array<{ element: string; success: boolean; error?: string }>;
  message: string;
}

export function quickEditStyle(
  req: QuickEditStyleRequest,
): Promise<QuickEditStyleResponse> {
  return api.post("/documents/quick-edit/style", req);
}

// ── Quick Edit: Table Operations ──────────────────────────────────

export interface TableOpRequest {
  path: string;
  workspace?: string;
  operation: string;
  sheet?: string;
  params: Record<string, unknown>;
}

export interface TableOpResponse {
  status: string;
  preview_html: string | null;
  message: string;
  error?: string;
}

export function quickEditTableOp(
  req: TableOpRequest,
): Promise<TableOpResponse> {
  return api.post("/documents/quick-edit/table-op", req);
}

// ── Quick Edit: PPT Theme ─────────────────────────────────────────

export function quickEditTheme(
  path: string,
  props: Record<string, string>,
  workspace = "",
): Promise<QuickEditStyleResponse> {
  return api.post("/documents/quick-edit/theme", { path, props, workspace });
}

// ── Quick Edit: Structure ──────────────────────────────────────────

export interface StructureOperation {
  command: "set" | "add" | "remove";
  path?: string;
  parent?: string;
  type?: string;
  props?: Record<string, unknown>;
}

export interface StructureEditRequest {
  path: string;
  workspace?: string;
  operations: StructureOperation[];
}

export interface StructureEditResponse {
  status: string;
  preview_html: string | null;
  results: Array<{ index: number; command?: string; target?: string; success: boolean; error?: string }>;
  message: string;
}

export function quickEditStructure(
  req: StructureEditRequest,
): Promise<StructureEditResponse> {
  return api.post("/documents/quick-edit/structure", req);
}

