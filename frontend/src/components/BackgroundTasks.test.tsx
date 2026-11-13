// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackgroundTasks } from "./BackgroundTasks";
import { cancelBackgroundTask, getBackgroundTask, listBackgroundTasks, listBackgroundGroups, getBackgroundGroup, cancelBackgroundGroup, getSemanticSummary, startSemanticSummary, getSummaryProvider, deleteSemanticSummary, listDeliveries, authorizeDelivery, revokeDelivery } from "../api/backgroundTasks";

vi.mock("../api/backgroundTasks", () => ({
  listDeliveries: vi.fn(), authorizeDelivery: vi.fn(), revokeDelivery: vi.fn(),
  listBackgroundTasks: vi.fn(), getBackgroundTask: vi.fn(), cancelBackgroundTask: vi.fn(),
  listBackgroundGroups: vi.fn(), getBackgroundGroup: vi.fn(), cancelBackgroundGroup: vi.fn(),
  getSemanticSummary: vi.fn(), startSemanticSummary: vi.fn(), cancelSemanticSummary: vi.fn(), getSummaryProvider: vi.fn(), deleteSemanticSummary: vi.fn(),
}));

const task = { id: "1", agent_name: "coder", task: "work", status: "running" };
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
beforeEach(() => {
  vi.mocked(listDeliveries).mockResolvedValue({ branch: "main", deliveries: [] });
  vi.mocked(getSemanticSummary).mockResolvedValue({ summary: null });
  vi.mocked(listBackgroundGroups).mockImplementation(async () => ({ branch: "main", groups: [] }));
});
afterEach(() => { document.body.innerHTML = ""; vi.resetAllMocks(); });

describe("background tasks", () => {
  it("shows status, untrusted result and cancellation", async () => {
    vi.mocked(listBackgroundTasks).mockResolvedValue({ branch: "main", tasks: [task] });
    vi.mocked(getBackgroundTask).mockResolvedValue({ ...task, result: "<script>unsafe</script>" });
    vi.mocked(cancelBackgroundTask).mockResolvedValue({ ...task, status: "cancelled", result: "Cancelled" });
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<BackgroundTasks sessionId="session" />); });
    await act(async () => { container.querySelector("button")!.click(); });
    expect(container.textContent).toContain("Running");
    const button = (label: string) => [...container.querySelectorAll("button")].find((b) => b.textContent === label)!;
    await act(async () => { button("View results").click(); });
    expect(getBackgroundTask).toHaveBeenCalledWith("session", "main", "1");
    expect(container.textContent).toContain("<script>unsafe</script>");
    expect(container.querySelector("script")).toBeNull();
    await act(async () => { button("Cancel task").click(); });
    expect(cancelBackgroundTask).toHaveBeenCalledWith("session", "main", "1");
    expect(container.textContent).toContain("Cancelled");
    await act(async () => root.unmount());
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("background polling races", () => {
  afterEach(() => vi.useRealTimers());
  it("ignores older branch responses and errors", async () => {
    vi.useFakeTimers();
    vi.mocked(listBackgroundGroups).mockResolvedValue({ branch: "new", groups: [] });
    vi.mocked(listDeliveries).mockResolvedValue({ branch: "new", deliveries: [] });
    const first = deferred<{ branch: string; tasks: typeof task[] }>();
    const second = deferred<{ branch: string; tasks: typeof task[] }>();
    vi.mocked(listBackgroundTasks).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<BackgroundTasks sessionId="s" />); });
    await act(async () => { container.querySelector("button")!.click(); vi.advanceTimersByTime(5000); });
    await act(async () => { second.resolve({ branch: "new", tasks: [{ ...task, task: "new branch" }] }); });
    await act(async () => { first.reject(new Error("late")); });
    expect(container.textContent).toContain("new branch");
    expect(container.textContent).not.toContain("Could not load background tasks");
    await act(async () => root.unmount());
  });
  it("clears the old branch and ignores its pending action", async () => {
    vi.mocked(listBackgroundGroups).mockResolvedValueOnce({ branch: "main", groups: [] }).mockResolvedValueOnce({ branch: "other", groups: [] });
    vi.mocked(listDeliveries).mockResolvedValueOnce({ branch: "main", deliveries: [] }).mockResolvedValueOnce({ branch: "other", deliveries: [] });
    const detail = deferred<typeof task & { result: string }>();
    vi.mocked(listBackgroundTasks).mockResolvedValueOnce({ branch: "main", tasks: [task] })
      .mockResolvedValueOnce({ branch: "other", tasks: [{ ...task, id: "2", task: "other branch" }] });
    vi.mocked(getBackgroundTask).mockReturnValueOnce(detail.promise);
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<BackgroundTasks sessionId="s" activeBranch="main" />); });
    await act(async () => { container.querySelector("button")!.click(); });
    await act(async () => { [...container.querySelectorAll("button")].find(b => b.textContent === "View results")!.click(); });
    await act(async () => { root.render(<BackgroundTasks sessionId="s" activeBranch="other" />); });
    await act(async () => { detail.resolve({ ...task, result: "stale result" }); });
    expect(container.textContent).toContain("other branch");
    expect(container.textContent).not.toContain("stale result");
    await act(async () => root.unmount());
  });
  it("rejects a response for a different active branch and resets on session switch", async () => {
    vi.mocked(listBackgroundTasks).mockResolvedValueOnce({ branch: "old", tasks: [task] })
      .mockResolvedValueOnce({ branch: "main", tasks: [{ ...task, task: "new session" }] });
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<BackgroundTasks sessionId="a" activeBranch="main" mobile />); });
    await act(async () => { container.querySelector("button")!.click(); });
    expect(container.textContent).toContain("The active branch changed; waiting for it to sync.");
    expect(container.textContent).not.toContain("work");
    await act(async () => { root.render(<BackgroundTasks sessionId="b" activeBranch="main" mobile />); });
    expect(container.textContent).toContain("new session");
    expect(listBackgroundTasks).toHaveBeenLastCalledWith("b");
    await act(async () => root.unmount());
  });
  it("does not let a pending poll undo cancellation", async () => {
    vi.useFakeTimers();
    const poll = deferred<{ branch: string; tasks: typeof task[] }>();
    vi.mocked(listBackgroundTasks).mockResolvedValueOnce({ branch: "main", tasks: [task] }).mockReturnValueOnce(poll.promise);
    vi.mocked(cancelBackgroundTask).mockResolvedValue({ ...task, status: "cancelled" });
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<BackgroundTasks sessionId="s" mobile />); });
    await act(async () => { container.querySelector("button")!.click(); vi.advanceTimersByTime(5000); });
    await act(async () => { [...container.querySelectorAll("button")].find(b => b.textContent === "Cancel task")!.click(); });
    await act(async () => { poll.resolve({ branch: "main", tasks: [task] }); });
    expect(container.textContent).toContain("Cancelled");
    await act(async () => root.unmount());
  });
});


describe.each([false, true])("background groups mobile=%s", (mobile) => {
  it("displays details safely and cancels a group", async () => {
    const group = { id: "g1", status: "running" };
    const detail = { ...group, steps: [{ step_id: "s1", agent_name: "coder", task: "work", depends_on: [], status: "running", job_id: "j", result: "<script>bad</script>" }], summary: null };
    vi.mocked(listBackgroundTasks).mockResolvedValue({ branch: "main", tasks: [] });
    vi.mocked(listBackgroundGroups).mockResolvedValue({ branch: "main", groups: [group] });
    vi.mocked(getBackgroundGroup).mockResolvedValue(detail);
    vi.mocked(cancelBackgroundGroup).mockResolvedValue({ ...detail, status: "cancelled" });
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<BackgroundTasks sessionId="s" mobile={mobile} />); });
    await act(async () => { container.querySelector("button")!.click(); });
    const button = (label: string) => [...container.querySelectorAll("button")].find(b => b.textContent === label)!;
    await act(async () => { button("View details").click(); });
    expect(getBackgroundGroup).toHaveBeenCalledWith("s", "main", "g1");
    expect(container.textContent).toContain("<script>bad</script>");
    expect(container.querySelector("script")).toBeNull();
    await act(async () => { button("Cancel task group").click(); });
    expect(cancelBackgroundGroup).toHaveBeenCalledWith("s", "main", "g1");
    expect(container.textContent).toContain("Cancelled");
    await act(async () => root.unmount());
  });
});


it("requires explicit consent and password before sending step results", async () => {
  const group = { id: "g", status: "succeeded", steps: [] };
  vi.mocked(listBackgroundTasks).mockResolvedValue({ branch: "main", tasks: [] });
  vi.mocked(listBackgroundGroups).mockResolvedValue({ branch: "main", groups: [group] });
  vi.mocked(getBackgroundGroup).mockResolvedValue(group);
  vi.mocked(getSummaryProvider).mockResolvedValue({ name: "Provider", type: "openai", model: "model", endpoint: "endpoint", proxy: "direct", binding: "binding" });
  vi.mocked(startSemanticSummary).mockResolvedValue({ summary: { status: "pending", text: "" } });
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  const button = (label: string) => [...container.querySelectorAll("button")].find(b => b.textContent === label)!;
  await act(async () => { root.render(<BackgroundTasks sessionId="s" />); });
  await act(async () => { container.querySelector("button")!.click(); });
  await act(async () => { button("View details").click(); });
  await act(async () => { button("Request semantic summary").click(); });
  expect(container.textContent).toContain("model provider");
  expect(container.textContent).toContain("Provider");
  expect(container.textContent).toContain("stored in plain text in the local database");
  expect(button("Confirm and generate").disabled).toBe(true);
  expect(startSemanticSummary).not.toHaveBeenCalled();
  await act(async () => root.unmount());
});


it("requires confirmation before clearing a terminal summary", async () => {
  const group = { id: "g", status: "succeeded", steps: [] };
  vi.mocked(listBackgroundTasks).mockResolvedValue({ branch: "main", tasks: [] });
  vi.mocked(listBackgroundGroups).mockResolvedValue({ branch: "main", groups: [group] });
  vi.mocked(getBackgroundGroup).mockResolvedValue(group);
  vi.mocked(getSemanticSummary).mockResolvedValue({ summary: { status: "succeeded", text: "private" } });
  vi.mocked(deleteSemanticSummary).mockResolvedValue({ deleted: true });
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  const button = (label: string) => [...container.querySelectorAll("button")].find(b => b.textContent === label)!;
  await act(async () => { root.render(<BackgroundTasks sessionId="s" />); });
  await act(async () => { container.querySelector("button")!.click(); });
  await act(async () => { button("View details").click(); });
  await act(async () => { button("Delete local semantic summary").click(); });
  expect(deleteSemanticSummary).not.toHaveBeenCalled();
  expect(container.textContent).toContain("It does not delete the original task results");
  await act(async () => { button("Confirm cleanup").click(); });
  expect(deleteSemanticSummary).toHaveBeenCalledWith("s", "main", "g");
  expect(container.textContent).not.toContain("private");
  await act(async () => root.unmount());
});

describe.each([false, true])("delivery authorization mobile=%s", (mobile) => {
  it("requires confirmation and supports revocation", async () => {
    const group = { id: "g1", status: "running" };
    vi.mocked(listBackgroundTasks).mockResolvedValue({ branch: "main", tasks: [] });
    vi.mocked(listBackgroundGroups).mockResolvedValue({ branch: "main", groups: [group] });
    vi.mocked(authorizeDelivery).mockResolvedValue({} as never);
    vi.mocked(revokeDelivery).mockResolvedValue({} as never);
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<BackgroundTasks sessionId="s" mobile={mobile} />); });
    expect(authorizeDelivery).not.toHaveBeenCalled();
    await act(async () => { container.querySelector("button")!.click(); });
    const button = (label: string) => [...container.querySelectorAll("button")].find(b => b.textContent === label)!;
    await act(async () => { button("Authorize auto-display").click(); });
    expect(authorizeDelivery).not.toHaveBeenCalled();
    await act(async () => { button("Authorize auto-display").click(); });
    expect(authorizeDelivery).toHaveBeenCalledWith("s", "main", "g1");
    expect(container.textContent).toContain("This task group will show a result card when it finishes.");
    await act(async () => { button("Revoke display authorization").click(); });
    expect(revokeDelivery).toHaveBeenCalledWith("s", "main", "g1");
    expect(container.textContent).toContain("Display authorization revoked.");
    confirm.mockRestore();
    await act(async () => root.unmount());
  });

  it("renders untrusted results as text and removes a revoked card", async () => {
    vi.mocked(listBackgroundTasks).mockResolvedValue({ branch: "main", tasks: [] });
    vi.mocked(listDeliveries).mockResolvedValue({ branch: "main", deliveries: [{ id: "g1", status: "succeeded", steps: [{ step_id: "step", status: "succeeded", result: "<script>unsafe</script>" }] }] });
    vi.mocked(revokeDelivery).mockResolvedValue({} as never);
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<BackgroundTasks sessionId="s" mobile={mobile} />); });
    expect(container.textContent).toContain("Unverified data");
    expect(container.textContent).toContain("it is not sent to the model");
    expect(container.textContent).toContain("<script>unsafe</script>");
    expect(container.querySelector("script")).toBeNull();
    expect(authorizeDelivery).not.toHaveBeenCalled();
    await act(async () => { container.querySelector<HTMLButtonElement>('article button')!.click(); });
    expect(revokeDelivery).toHaveBeenCalledWith("s", "main", "g1");
    expect(container.querySelector('article')).toBeNull();
    await act(async () => root.unmount());
  });
});
