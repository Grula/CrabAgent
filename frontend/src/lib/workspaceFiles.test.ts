import { describe, expect, it } from "vitest";
import {
  CLOSE_DOC_DISCARD_TEST_ID,
  closeDocDiscardPrompt,
  fileExtension,
  forgetDiscardedMarkdownEdits,
  forgetLeaveWorkDiscardedMarkdownEdits,
  hasUnsavedEditorEdits,
  isCodeFile,
  needsDiscardConfirmForDocClose,
  needsDiscardConfirmForDocSwitch,
  needsDiscardConfirmForLeaveWork,
  needsDiscardConfirmForSessionSwitch,
  needsDiscardConfirmForWorkTabSwitch,
  needsOfficePreview,
  opensInWorkspace,
  queuePendingPrompt,
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
    // `.svg` is an image, not code or a document.
    expect(workspaceFileKind("diagram.svg")).toBe("image");
    expect(workspaceFileKind("diagram.svg")).not.toBe("code");
  });

  it("only asks for an Office preview for office and unknown files", () => {
    // Code, Markdown and HTML render in their own panel: no preview request.
    expect(needsOfficePreview("small.ts")).toBe(false);
    expect(needsOfficePreview("note.md")).toBe(false);
    expect(needsOfficePreview("page.html")).toBe(false);
    // An image renders in ImagePanel, so no preview request either.
    expect(needsOfficePreview("logo.png")).toBe(false);
    // Office documents still go through the preview service.
    expect(needsOfficePreview("report.docx")).toBe(true);
    // An extension no panel owns keeps the previous preview behaviour.
    expect(needsOfficePreview("archive.zip")).toBe(true);
    expect(needsOfficePreview("README")).toBe(true);
  });

  it("classifies images as their own openable kind", () => {
    for (const name of ["logo.png", "photo.jpg", "icon.svg", "banner.webp", "shot.JPEG"]) {
      expect(opensInWorkspace(name)).toBe(true);
      expect(workspaceFileKind(name)).toBe("image");
      // An image must never be sent to the editor or the office preview.
      expect(isCodeFile(name)).toBe(false);
      expect(needsOfficePreview(name)).toBe(false);
    }
  });

  it("does not offer files it cannot open", () => {
    expect(opensInWorkspace("archive.zip")).toBe(false);
    expect(fileExtension("small.ts")).toBe("ts");
    expect(fileExtension("SHEET.XLSX")).toBe("xlsx");
    expect(fileExtension("README")).toBe("");
  });

  it("asks before a dirty editor is unmounted by a Work tab switch", () => {
    // Documents hosts the editor: any other tab unmounts it and drops the buffers.
    for (const nextTab of ["timeline", "calendar", "browser", "meeting"]) {
      expect(needsDiscardConfirmForWorkTabSwitch({ currentTab: "documents", nextTab, codeDirty: true, markdownDirty: false })).toBe(true);
    }
    // Clicking the tab you are already on changes nothing.
    expect(needsDiscardConfirmForWorkTabSwitch({ currentTab: "documents", nextTab: "documents", codeDirty: true, markdownDirty: false })).toBe(false);
  });

  it("switches Work tabs without asking when the editor is clean", () => {
    expect(needsDiscardConfirmForWorkTabSwitch({ currentTab: "documents", nextTab: "timeline", codeDirty: false, markdownDirty: false })).toBe(false);
  });

  it("does not ask for Work tab switches that do not leave Documents", () => {
    for (const currentTab of ["timeline", "calendar", "browser", "meeting"]) {
      expect(needsDiscardConfirmForWorkTabSwitch({ currentTab, nextTab: "documents", codeDirty: true, markdownDirty: false })).toBe(false);
    }
  });

  it("asks before a Work tab switch drops a dirty Markdown document", () => {
    // Documents hosts MarkdownPanel too: Timeline and friends unmount it.
    expect(needsDiscardConfirmForWorkTabSwitch({
      currentTab: "documents", nextTab: "timeline", codeDirty: false, markdownDirty: true,
    })).toBe(true);
    // A clean document still switches straight away.
    expect(needsDiscardConfirmForWorkTabSwitch({
      currentTab: "documents", nextTab: "timeline", codeDirty: false, markdownDirty: false,
    })).toBe(false);
  });

  it("asks before a chat switch would carry a dirty buffer to another workspace", () => {
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: true, markdownDirty: false })).toBe(true);
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: false, markdownDirty: false })).toBe(false);
  });

  it("asks before a chat switch drops a dirty Markdown document", () => {
    // The session-change effect clears openDocs and the dirty set: text would be gone.
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: false, markdownDirty: true })).toBe(true);
  });

  it("asks before switching away from a dirty Markdown document", () => {
    const dirty = new Set(["a.md"]);
    // Clicking another document remounts the panel (it is keyed by path).
    expect(needsDiscardConfirmForDocSwitch({ currentPath: "a.md", nextPath: "b.md", markdownDirtyPaths: dirty })).toBe(true);
    // So does the code chip or the empty state.
    expect(needsDiscardConfirmForDocSwitch({ currentPath: "a.md", nextPath: "src/x.ts", markdownDirtyPaths: dirty })).toBe(true);
    expect(needsDiscardConfirmForDocSwitch({ currentPath: "a.md", nextPath: null, markdownDirtyPaths: dirty })).toBe(true);
  });

  it("switches documents without asking when the one on screen is clean", () => {
    // Nothing dirty: no prompt, no extra render.
    expect(needsDiscardConfirmForDocSwitch({ currentPath: "a.md", nextPath: "b.md", markdownDirtyPaths: new Set() })).toBe(false);
    // Clean because it was just saved, even though another document is dirty.
    expect(needsDiscardConfirmForDocSwitch({
      currentPath: "b.md", nextPath: "a.md", markdownDirtyPaths: new Set(["a.md"]),
    })).toBe(false);
    // Re-clicking the document already shown changes nothing.
    expect(needsDiscardConfirmForDocSwitch({
      currentPath: "a.md", nextPath: "a.md", markdownDirtyPaths: new Set(["a.md"]),
    })).toBe(false);
    // No document on screen: there is nothing to leave.
    expect(needsDiscardConfirmForDocSwitch({
      currentPath: null, nextPath: "a.md", markdownDirtyPaths: new Set(["a.md"]),
    })).toBe(false);
  });

  it("asks before closing a dirty document, not a clean one", () => {
    const dirty = new Set(["docs/a.md"]);
    // The tab's X and the panel's X both unmount the panel and drop its tab.
    expect(needsDiscardConfirmForDocClose({ closingPath: "docs/a.md", markdownDirtyPaths: dirty })).toBe(true);
    // A clean document closes straight away, even while another one is dirty.
    expect(needsDiscardConfirmForDocClose({ closingPath: "docs/b.md", markdownDirtyPaths: dirty })).toBe(false);
    expect(needsDiscardConfirmForDocClose({ closingPath: "docs/a.md", markdownDirtyPaths: new Set() })).toBe(false);
    // Nothing on screen: there is nothing to ask about.
    expect(needsDiscardConfirmForDocClose({ closingPath: null, markdownDirtyPaths: dirty })).toBe(false);
  });

  it("keeps a cancelled close open and dirty, and forgets a discarded one", () => {
    const dirty = new Set(["docs/a.md"]);
    // Cancel never runs the close, so the tab stays open and still reports dirty.
    expect(needsDiscardConfirmForDocClose({ closingPath: "docs/a.md", markdownDirtyPaths: dirty })).toBe(true);
    expect(dirty.has("docs/a.md")).toBe(true);
    // Discard runs the close, which forgets the unmounted panel's report.
    forgetDiscardedMarkdownEdits(dirty, ["docs/a.md"]);
    expect(dirty.has("docs/a.md")).toBe(false);
    // The closed document no longer counts when the page asks about leaving Work.
    expect(needsDiscardConfirmForLeaveWork({ codeDirty: false, markdownDirty: dirty.size > 0 })).toBe(false);
  });

  it("forgets only the discarded paths", () => {
    const dirty = new Set(["docs/a.md", "docs/b.md"]);
    // A document left behind by a Work-tab switch is gone; a missing path (no
    // document on screen) must not clear anything.
    forgetDiscardedMarkdownEdits(dirty, ["docs/a.md", null, undefined]);
    expect([...dirty]).toEqual(["docs/b.md"]);
    expect(needsDiscardConfirmForLeaveWork({ codeDirty: false, markdownDirty: dirty.size > 0 })).toBe(true);
  });

  it("gives the close confirmation its own test id and names the closed file", () => {
    const prompt = closeDocDiscardPrompt("/home/me/proj/docs/a.md");
    expect(prompt.confirmTestId).toBe(CLOSE_DOC_DISCARD_TEST_ID);
    expect(prompt.confirmTestId).not.toBe("work-tab-discard-confirm");
    expect(prompt.dirtyName).toBe("a.md");
    // The close path only ever closes a document, so it must name that file.
    expect(prompt.description).toContain("a.md");
    expect(prompt.description).not.toMatch(/editor/i);
    expect(prompt.body).not.toMatch(/editor/i);
    // No path (an agent-started preview) still reads as a sentence.
    expect(closeDocDiscardPrompt(null).dirtyName).toBe("this file");
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
      currentTab: "documents", nextTab: "timeline", codeDirty: hasUnsavedEditorEdits({ dirty: true, anyDirty: false }), markdownDirty: false,
    })).toBe(true);
  });

  it("asks before the first message creates a session over a dirty editor", () => {
    // No active session is legal next to an open editor: creating the chat clears its tabs.
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: hasUnsavedEditorEdits({ dirty: true, anyDirty: false }), markdownDirty: false })).toBe(true);
    expect(needsDiscardConfirmForSessionSwitch({ codeDirty: hasUnsavedEditorEdits(null), markdownDirty: false })).toBe(false);
  });

  it("asks before leaving Work with a dirty code buffer", () => {
    expect(needsDiscardConfirmForLeaveWork({ codeDirty: true, markdownDirty: false })).toBe(true);
    // The page feeds hasUnsavedEditorEdits straight in, like the other guards.
    expect(needsDiscardConfirmForLeaveWork({
      codeDirty: hasUnsavedEditorEdits({ dirty: false, anyDirty: true }),
      markdownDirty: false,
    })).toBe(true);
  });

  it("asks before leaving Work with a dirty Markdown document", () => {
    expect(needsDiscardConfirmForLeaveWork({ codeDirty: false, markdownDirty: true })).toBe(true);
  });

  it("leaves Work without asking when code and Markdown are both clean", () => {
    expect(needsDiscardConfirmForLeaveWork({ codeDirty: false, markdownDirty: false })).toBe(false);
  });

  it("asks before leaving Work when code and Markdown are both dirty", () => {
    expect(needsDiscardConfirmForLeaveWork({ codeDirty: true, markdownDirty: true })).toBe(true);
  });

  it("discarding on leave-Work forgets every dirty document, not just the named one", () => {
    const dirty = new Set(["docs/a.md", "docs/b.md"]);
    // Cancel only closes the dialog: nothing is forgotten, so the page still asks.
    expect(needsDiscardConfirmForLeaveWork({ codeDirty: false, markdownDirty: dirty.size > 0 })).toBe(true);
    // Discard unmounts every Documents panel, so every dirty report is stale.
    forgetLeaveWorkDiscardedMarkdownEdits(dirty);
    expect([...dirty]).toEqual([]);
    // Nothing is left to ask about a panel that is gone.
    expect(needsDiscardConfirmForLeaveWork({ codeDirty: false, markdownDirty: dirty.size > 0 })).toBe(false);
    // An already clean page stays clean (and the helper does not shift entries).
    forgetLeaveWorkDiscardedMarkdownEdits(dirty);
    expect([...dirty]).toEqual([]);
  });

  it("refuses a second pending prompt from any entry point while one is open", () => {
    type Request = { source: "discard" | "sessionSwitch" | "leaveWork" };
    const sources = ["discard", "sessionSwitch", "leaveWork"] as const;
    // Regression: with nothing pending, each request becomes the prompt.
    for (const source of sources) {
      expect(queuePendingPrompt(null, { source })).toEqual({ source });
    }
    // A discard dialog is open; browser Back (a hashchange) asks for a Work tab
    // or a chat switch, or a document close asks again. Each later request is
    // dropped, never queued behind the open one.
    const open: Request = { source: "discard" };
    for (const source of sources) {
      expect(queuePendingPrompt(open, { source })).toBe(open);
    }
    // Once the open prompt is answered (state null) the next request is accepted.
    expect(queuePendingPrompt(null, { source: "leaveWork" })).toEqual({ source: "leaveWork" });
  });
});
