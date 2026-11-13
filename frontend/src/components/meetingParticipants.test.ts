import { describe, it, expect } from "vitest";

import type { AgentProfile } from "../api/agents";
import {
  buildParticipantOptions,
  toggleParticipant,
} from "./meetingParticipants";

/** Minimal agent fixture; only the fields the utilities read are meaningful. */
function makeAgent(overrides: Partial<AgentProfile> & { name: string }): AgentProfile {
  return {
    display_name: overrides.name,
    icon: "🤖",
    enabled: true,
    ...overrides,
  } as AgentProfile;
}

describe("buildParticipantOptions", () => {
  it("R1 includes only enabled agents", () => {
    const agents = [
      makeAgent({ name: "alice", enabled: true }),
      makeAgent({ name: "bob", enabled: false }),
      makeAgent({ name: "carol", enabled: true }),
    ];

    const options = buildParticipantOptions(agents);

    expect(options.map((o) => o.value)).toEqual(["alice", "carol"]);
  });

  it("R2 uses the agent name as the option value", () => {
    const agents = [
      makeAgent({ name: "researcher", display_name: "Researcher" }),
      makeAgent({ name: "writer", display_name: "Writer" }),
    ];

    const options = buildParticipantOptions(agents);

    expect(options.map((o) => o.value)).toEqual(["researcher", "writer"]);
  });

  it("R3 falls back to the name when display_name is falsy", () => {
    const agents = [makeAgent({ name: "alice", display_name: "" })];

    const options = buildParticipantOptions(agents);

    expect(options[0].label).toBe("alice");
  });

  it('R4 falls back to 🤖 when icon is falsy', () => {
    const agents = [makeAgent({ name: "alice", icon: "" })];

    const options = buildParticipantOptions(agents);

    expect(options[0].icon).toBe("🤖");
  });

  it("R5 preserves input order and returns [] for empty input", () => {
    const agents = [
      makeAgent({ name: "zeta" }),
      makeAgent({ name: "alpha" }),
      makeAgent({ name: "mike" }),
    ];

    expect(buildParticipantOptions(agents).map((o) => o.value)).toEqual([
      "zeta",
      "alpha",
      "mike",
    ]);
    expect(buildParticipantOptions([])).toEqual([]);
  });
});

describe("toggleParticipant", () => {
  it("R6 appends the value when absent", () => {
    const next = toggleParticipant(["alice"], "bob");

    expect(next).toEqual(["alice", "bob"]);
  });

  it("R7 removes the value when present", () => {
    const next = toggleParticipant(["alice", "bob", "carol"], "bob");

    expect(next).toEqual(["alice", "carol"]);
  });

  it("R8 does not mutate the input array", () => {
    const original = Object.freeze(["alice", "bob"]) as unknown as string[];
    const snapshot = [...original];

    const added = toggleParticipant(original, "carol");
    const removed = toggleParticipant(original, "alice");

    expect(original).toEqual(snapshot);
    expect(original).toEqual(["alice", "bob"]);
    expect(added).not.toBe(original);
    expect(removed).not.toBe(original);
  });

  it("R9 ignores empty and whitespace-only values", () => {
    const participants = ["alice"];

    expect(toggleParticipant(participants, "")).toEqual(["alice"]);
    expect(toggleParticipant(participants, "  ")).toEqual(["alice"]);
    expect(toggleParticipant([], "")).toEqual([]);
  });
});
