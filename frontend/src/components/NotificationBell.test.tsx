// @vitest-environment jsdom
// Regression test: after the notification panel moved to a portal, clicks inside the panel must not be misread as outside clicks.
// Previously the mousedown handler excluded only the bell button and not the portal panel, so panel contents were unclickable.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NotificationBell from "./NotificationBell";
import type { Notification } from "../api/notifications";

vi.mock("../api/notifications", () => ({
  listNotifications: vi.fn(async () => [
    {
      id: 1,
      title: "Monitoring task completed",
      body: "Task completed",
      read: false,
      created_at: "2026-11-04T10:00:00Z",
      conversation_id: "sess-1",
    } satisfies Notification,
  ]),
  unreadCount: vi.fn(async () => ({ count: 1 })),
  markRead: vi.fn(async () => ({ status: "ok" })),
  markAllRead: vi.fn(async () => ({ status: "ok" })),
}));

vi.mock("../hooks/useWorkStatus", () => ({
  useWorkStatus: () => ({ connected: true }),
}));

let container: HTMLDivElement;
let root: Root;

function renderBell() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <NotificationBell onSwitchSession={vi.fn()} onNotificationAction={vi.fn()} />,
    );
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

async function openPanel() {
  const bell = container.querySelector("button")!;
  await act(async () => {
    bell.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  // Wait for the render after fetchList
  await act(async () => {
    await Promise.resolve();
  });
}

describe("NotificationBell portal panel", () => {
  it("keeps the panel open when clicking inside it", async () => {
    renderBell();
    await openPanel();

    const panel = document.querySelector("body > .fixed") as HTMLElement;
    expect(panel).toBeTruthy();

    // Simulate one full click inside the panel (mousedown → mouseup → click)
    const card = panel.querySelector(".cursor-pointer") as HTMLElement;
    expect(card).toBeTruthy();
    await act(async () => {
      card.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    await act(async () => {
      await Promise.resolve();
    });

    // Key assertion: the panel must not close on a mousedown inside the panel
    expect(document.querySelector("body > .fixed")).not.toBeNull();
  });

  it("closes the panel on mousedown outside the bell and panel", async () => {
    renderBell();
    await openPanel();
    expect(document.querySelector("body > .fixed")).not.toBeNull();

    await act(async () => {
      document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(document.querySelector("body > .fixed")).toBeNull();
  });
});
