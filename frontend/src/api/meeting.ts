import { api } from "./client";

export interface MeetingNote {
  id: string;
  timestamp: string;
  speaker: string;
  content: string;
}

export interface MeetingState {
  title: string;
  participants: string[];
  notes: MeetingNote[];
  status: string;
}

export interface EndMeetingResult {
  session_id: string;
  summary: string;
  created_tasks: {
    id: number;
    title: string;
    assignee: string;
    priority: string;
    deadline?: string;
  }[];
  status: string;
}

export function getMeeting(sessionId: string): Promise<MeetingState> {
  return api.get(`/meeting/${sessionId}`);
}

export function saveMeeting(
  sessionId: string,
  state: { title: string; participants: string[]; notes: MeetingNote[] },
): Promise<MeetingState> {
  return api.put(`/meeting/${sessionId}`, state);
}

export function endMeeting(
  sessionId: string,
  state: { title: string; participants: string[]; notes: MeetingNote[] },
): Promise<EndMeetingResult> {
  return api.post(`/meeting/${sessionId}/end`, state);
}
