import type { AgentProfile } from "../api/agents";

export interface ParticipantOption {
  value: string; // agent.name
  label: string; // agent.display_name || agent.name
  icon: string;  // agent.icon || "🤖"
}

export function buildParticipantOptions(agents: AgentProfile[]): ParticipantOption[] {
  return agents
    .filter((agent) => agent.enabled === true)
    .map((agent) => ({
      value: agent.name,
      label: agent.display_name || agent.name,
      icon: agent.icon || "🤖",
    }));
}

export function toggleParticipant(participants: string[], value: string): string[] {
  if (!value.trim()) return [...participants];
  return participants.includes(value)
    ? participants.filter((item) => item !== value)
    : [...participants, value];
}
