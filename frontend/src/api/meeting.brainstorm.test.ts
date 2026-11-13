// A: meeting brainstorm API client.
// brainstormMeeting(sessionId, topic, participants)
//   -> POST /meeting/<sessionId>/brainstorm  with the {topic, participants} body
//   -> resolves with the parsed {session_id, ideas, reactions} payload
import { beforeEach, describe, expect, it, vi } from "vitest";
import { brainstormMeeting, type BrainstormResult } from "./meeting";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
}));

vi.mock("./client", () => ({
  api: { get: mocks.get, put: mocks.put, post: mocks.post },
}));

const RESULT: BrainstormResult = {
  session_id: "sess-1",
  ideas: [{ agent: "coder", ideas: ["Use a feature flag for the rollout"] }],
  reactions: [
    {
      agent: "coder",
      reactions: [
        { idea_index: 0, verdict: "agree", comment: "Yes, ship it behind a flag" },
      ],
    },
  ],
};

beforeEach(() => {
  mocks.get.mockReset();
  mocks.put.mockReset();
  mocks.post.mockReset();
});

describe("brainstormMeeting", () => {
  it("POSTs /meeting/<sessionId>/brainstorm with the topic and participants, and returns the result", async () => {
    mocks.post.mockResolvedValue(RESULT);

    const result = await brainstormMeeting("sess-1", "Adopt a feature-flag rollout", ["coder", "writer"]);

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post.mock.calls[0][0]).toBe("/meeting/sess-1/brainstorm");
    expect(mocks.post.mock.calls[0][1]).toEqual({
      topic: "Adopt a feature-flag rollout",
      participants: ["coder", "writer"],
    });
    // It must not fall back to the other meeting verbs.
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.put).not.toHaveBeenCalled();

    // The parsed payload is handed back unchanged.
    expect(result).toEqual(RESULT);
    expect(result.session_id).toBe("sess-1");
    expect(result.ideas[0].ideas).toEqual(["Use a feature flag for the rollout"]);
    expect(result.reactions[0].reactions[0].verdict).toBe("agree");
    expect(result.reactions[0].reactions[0].idea_index).toBe(0);
  });

  it("keeps the session id verbatim and still sends an empty participants array", async () => {
    mocks.post.mockResolvedValue({ session_id: "m-77", ideas: [], reactions: [] });

    const result = await brainstormMeeting("m-77", "Retro topics", []);

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post.mock.calls[0][0]).toBe("/meeting/m-77/brainstorm");
    expect(mocks.post.mock.calls[0][1]).toEqual({ topic: "Retro topics", participants: [] });
    expect(result.ideas).toEqual([]);
    expect(result.reactions).toEqual([]);
  });
});
