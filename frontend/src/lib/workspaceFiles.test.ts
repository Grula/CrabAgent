import { describe, expect, it } from "vitest";
import {
  fileExtension,
  hasUnsavedEditorEdits,
  isCodeFile,
  needsDiscardConfirmForSessionSwitch,
  needsDiscardConfirmForWorkTabSwitch,
  needsOfficePreview,
  opensInWorkspace,
  workspaceFileKind,
} from "./workspaceFiles";

describe("workspaceFiles", () => {
  it("classifies code files as openable in the workspace", () => {
    for (const name of ["small.ts", "app.py", "package.json", "main.go", "styles.css"]) {
      expect(opensInWorkspace(name)).toBe(true);
      expect(isCodeFile(name)).toBe(true);
      expect(workspaceFileKind(name)).toBe("code");
    }
  });

  it("keeps the existing office, markdown and prototype routing", () => {
    expect(workspaceFileKind("report.docx")).toBe("document");
    expect(workspaceFileKind("sheet.xlsx")).toBe("document");
    expect(workspaceFileKind("slides.pptx")).toBe("document");
    expect(workspaceFileKind("note.md")).toBe("markdown");
    expect(workspaceFileKind("page.HTML")).toBe("prototype");
  });

  it("only asks for an Office preview for office and unknown files", () => {
    // Code, Markdown and HTML render in their own panel: no preview request.
    expect(needsOfficePreview("small.ts")).toBe(false);
    expect(needsOfficePreview("note.md")).toBe(false);
    expect(needsOfficePreview("page.html")).toBe(false);
    // Office documents still go through the preview service.
    expect(needsOfficePreview("report.docx")).toBe(true);
    // An extension no panel owns keeps the previous preview behaviour.
    expect(needsOfficePreview("archive.zip")).toBe(true);
    expect(needsOfficePreview("README")).toBe(true);
  });

  it("does not offer files it cannot open", () => {
    expect(opensInWorkspace("logo.png")).toBe(false);
    expect(opensInWorkspace("archive.zip")).toBe(false);
    expect(fileExtension("small.ts")).toBe("ts");
    expect(fileExtension("SHEET.XLSX")).toBe("xlsx");
    expect(fileExtension("README")).toBe("");
  });

  it("asks before a dirty editor is unmounted by a Work tab switch", () => {
    // Documents hosts the editor: any other tab unmounts it and drops the buffers.
    for (const nextTab of ["timeline", "calendar", "browser", "meeting"]) {
      expect(needsDiscardConfirmForWorkTabSwitch({ currentTab: "documents", nextTab, codeDirty: true })).toBe(true);
    }
    // Clicking the tab you are already on changes nothing.
    expect(needsDiscardConfirmForWorkTabSwitch({ currentTab: "documents", nextTab: "documents", codeDirty: true })).toBe(false);
  });

  it("switches Work tabs without asking when the editor is clean", () => {
    expect(needsDiscardConfirmForWorkTabSwitch({ currentTab: "documents", nextTab: "timeline", codeDirty: false })).toBe(false);
  });

  it("does not ask for Work tab switches that do not leave Documents", () => {
    for (const currentTab of ["timeline", "calendar", "browser", "meeting"]) {
      expect(needsDiscardConfirmForWorkTabSwitch({ currentTab, nextTab: "documents", codeDirty: true })).toBe(false);
    }
  });

  it("asks before a chat switch would carry a dirty buffer to another workspace", () => {
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: true })).toBe(true);
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: false })).toBe(false);
  });

  it("treats a missing or clean editor snapshot as nothing to lose", () => {
    // The page clears its ref when CodePanel unmounts, so null must not ask.
    expect(hasUnsavedEditorEdits(null)).toBe(false);
    expect(hasUnsavedEditorEdits(undefined)).toBe(false);
    expect(hasUnsavedEditorEdits({ dirty: false, anyDirty: false })).toBe(false);
    expect(hasUnsavedEditorEdits({ dirty: true, anyDirty: false })).toBe(true);
    // Another tab is dirty while the active one is not.
    expect(hasUnsavedEditorEdits({ dirty: false, anyDirty: true })).toBe(true);
  });

  it("asks before the agent timeline unmounts a dirty editor", () => {
    // AgentRunBoard's timeline action leaves Documents the same way the tab bar does.
    expect(needsDiscardConfirmForWorkTabSwitch({
      currentTab: "documents", nextTab: "timeline", codeDirty: hasUnsavedEditorEdits({ dirty: true, anyDirty: false }),
    })).toBe(true);
  });

  it("asks before the first message creates a session over a dirty editor", () => {
    // No active session is legal next to an open editor: creating the chat clears its tabs.
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: hasUnsavedEditorEdits({ dirty: true, anyDirty: false }) })).toBe(true);
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: hasUnsavedEditorEdits(null) })).toBe(false);
  });
});
