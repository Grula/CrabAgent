// F1: meeting persistence API client.
// getMeeting  -> GET  /meeting/<id>
// saveMeeting -> PUT  /meeting/<id>      with the {title, participants, notes} body
// endMeeting  -> POST /meeting/<id>/end  with the {title, participants, notes} body
import { beforeEach, describe, expect, it, vi } from "vitest";
import { endMeeting, getMeeting, saveMeeting, type MeetingNote } from "./meeting";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
}));

vi.mock("./client", () => ({
  api: { get: mocks.get, put: mocks.put, post: mocks.post },
}));

const NOTE: MeetingNote = {
  id: "n-1",
  timestamp: "10:15",
  speaker: "Coder",
  content: "Agreed on the release scope",
};

const STATE = { title: "Release sync", participants: ["coder"], notes: [NOTE] };

beforeEach(() => {
  mocks.get.mockReset();
  mocks.put.mockReset();
  mocks.post.mockReset();
});

describe("getMeeting", () => {
  it("GETs /meeting/<sessionId> and returns the loaded meeting state", async () => {
    const state = { ...STATE, status: "active" };
    mocks.get.mockResolvedValue(state);

    const result = await getMeeting("sess-1");

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get.mock.calls[0][0]).toBe("/meeting/sess-1");
    expect(result).toEqual(state);
  });
});

describe("saveMeeting", () => {
  it("PUTs /meeting/<sessionId> with the given title, participants and notes", async () => {
    mocks.put.mockResolvedValue({ ...STATE, status: "active" });

    await saveMeeting("sess-2", STATE);

    expect(mocks.put).toHaveBeenCalledTimes(1);
    expect(mocks.put.mock.calls[0][0]).toBe("/meeting/sess-2");
    expect(mocks.put.mock.calls[0][1]).toEqual(STATE);
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("still sends empty participants and notes arrays (does not drop them)", async () => {
    const empty = { title: "", participants: [], notes: [] };
    mocks.put.mockResolvedValue({ ...empty, status: "active" });

    await saveMeeting("sess-3", empty);

    expect(mocks.put.mock.calls[0][0]).toBe("/meeting/sess-3");
    expect(mocks.put.mock.calls[0][1]).toEqual(empty);
  });
});

describe("endMeeting", () => {
  it("POSTs /meeting/<sessionId>/end with the state and returns summary plus created tasks", async () => {
    const result = {
      session_id: "sess-4",
      summary: "Meeting summary text",
      created_tasks: [
        { id: 11, title: "Write smoke tests", assignee: "coder", priority: "high" },
        { id: 12, title: "Draft release notes", assignee: "coder", priority: "medium", deadline: "2026-12-01" },
      ],
      status: "ended",
    };
    mocks.post.mockResolvedValue(result);

    const ended = await endMeeting("sess-4", STATE);

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post.mock.calls[0][0]).toBe("/meeting/sess-4/end");
    expect(mocks.post.mock.calls[0][1]).toEqual(STATE);
    expect(mocks.put).not.toHaveBeenCalled();
    expect(ended.summary).toBe("Meeting summary text");
    expect(ended.created_tasks.map((task) => task.title)).toEqual([
      "Write smoke tests",
      "Draft release notes",
    ]);
  });
});
