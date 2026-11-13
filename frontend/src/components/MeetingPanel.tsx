import { useState, useCallback, useRef, useEffect } from "react";
import { Clock, Users, CheckCircle2, AlertCircle, Sparkles, Send, FileText, X } from "lucide-react";
import { cn } from "../lib/cn";
import * as sessionsApi from "../api/sessions";
import { listAgentProfiles } from "../api/agents";
import {
  getMeeting,
  saveMeeting,
  endMeeting,
  type EndMeetingResult,
  type MeetingNote,
} from "../api/meeting";
import { buildParticipantOptions, type ParticipantOption } from "./meetingParticipants";
import { ParticipantPicker } from "./ParticipantPicker";

interface AISuggestion {
  type: "task" | "question" | "decision";
  content: string;
}

interface Props {
  sessionId: string;
  className?: string;
  onPrompt?: (text: string) => void;
}

export function MeetingPanel({ sessionId, className, onPrompt }: Props) {
  const [title, setTitle] = useState("");
  const [participants, setParticipants] = useState<string[]>([]);
  const [participantOptions, setParticipantOptions] = useState<ParticipantOption[]>([]);
  const [notes, setNotes] = useState<MeetingNote[]>([]);
  const [inputText, setInputText] = useState("");
  const [inputSpeaker, setInputSpeaker] = useState("");
  const [aiSuggestions, setAiSuggestions] = useState<AISuggestion[]>([]);
  const [status, setStatus] = useState<"active" | "ended">("active");
  const [ending, setEnding] = useState(false);
  const [summary, setSummary] = useState("");
  const [createdTasks, setCreatedTasks] = useState<EndMeetingResult["created_tasks"]>([]);
  const notesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => { notesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [notes]);

  useEffect(() => {
    listAgentProfiles()
      .then((list) => setParticipantOptions(buildParticipantOptions(list)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    getMeeting(sessionId)
      .then((state) => {
        if (!state) return;
        setTitle(state.title ?? "");
        setParticipants(state.participants ?? []);
        setNotes(state.notes ?? []);
        setStatus(state.status === "ended" ? "ended" : "active");
      })
      .catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    if (status !== "active") return;
    const timer = setTimeout(() => {
      saveMeeting(sessionId, { title, participants, notes }).catch(() => {});
    }, 500);
    return () => clearTimeout(timer);
  }, [sessionId, status, title, participants, notes]);

  const addNote = useCallback(() => {
    const text = inputText.trim();
    if (!text) return;
    const now = new Date();
    const ts = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;
    const note: MeetingNote = { id: `m-${Date.now()}`, timestamp: ts, speaker: inputSpeaker || "Me", content: text };
    setNotes((prev) => [...prev, note]);
    setInputText("");

    // Auto-analyze every 5 notes
    if ((notes.length + 1) % 5 === 0) {
      analyzeNotes([...notes, note]);
    }
  }, [inputText, inputSpeaker, notes]);

  const analyzeNotes = useCallback(async (allNotes: MeetingNote[]) => {
    const recent = allNotes.slice(-10);
    const text = recent.map((n) => `[${n.timestamp}] ${n.speaker}: ${n.content}`).join("\n");
    const prompt = `Here are the meeting notes:\n\n${text}\n\nAnalyse:\n1. What action items are there (things a person must do)? Format: {title, assignee, priority}\n2. What questions are still open?\n3. What decisions are explicit?\n\nReturn JSON only.`;

    try {
      await sessionsApi.sendPrompt(sessionId, prompt);
    } catch {}
  }, [sessionId]);

  const handleEnd = useCallback(() => {
    const notesText = notes.map((n) => `[${n.timestamp}] ${n.speaker}: ${n.content}`).join("\n");
    const labels = participants
      .map((item) => participantOptions.find((option) => option.value === item)?.label ?? item)
      .join(", ");
    const participantsPart = participants.length > 0 ? `Participants: ${labels}\n\n` : "";
    const prompt = `The meeting has ended. Write the minutes from the notes below: summary, action items, open questions, and decisions.\n\n${participantsPart}${notesText}`;
    onPrompt?.(prompt);

    setEnding(true);
    endMeeting(sessionId, { title, participants, notes })
      .then((result) => {
        setSummary(result.summary);
        setCreatedTasks(result.created_tasks ?? []);
        setStatus("ended");
      })
      .catch(() => setStatus("ended"))
      .finally(() => setEnding(false));
  }, [notes, onPrompt, participants, participantOptions, sessionId, title]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); addNote(); }
  };

  return (
    <div className={cn("flex flex-col h-full bg-[var(--bg-primary)]", className)}>
      {/* Header */}
      <div className="px-3 py-2 border-b border-[var(--border)] bg-[var(--bg-secondary)] shrink-0">
        <div className="flex items-center gap-2 mb-1">
          <span className={cn("w-2 h-2 rounded-full", status === "active" ? "bg-[var(--success)] animate-pulse" : "bg-[var(--text-tertiary)]")} />
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={"Meeting title…"}
            className="flex-1 bg-transparent text-sm font-medium text-[var(--text-primary)] border-0 outline-none placeholder:text-[var(--text-tertiary)]"
          />
          {status === "active" && (
            <button onClick={handleEnd} className="flex items-center gap-1 px-2 py-1 rounded text-[10px] bg-[var(--danger-bg)] text-[var(--danger)] hover:bg-[var(--danger-border)] transition-colors">
              <X size={12} /> {"End Meeting"}
            </button>
          )}
          {ending && (
            <span className="text-[10px] text-[var(--text-tertiary)]">{"Generating summary…"}</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11px] text-[var(--text-tertiary)]">
          <Clock size={12} /> <span>{new Date().toLocaleString()}</span>
          <Users size={12} className="ml-2" />
          <ParticipantPicker
            value={participants}
            options={participantOptions}
            onChange={setParticipants}
          />
        </div>
      </div>

      {/* Notes area */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {notes.map((note) => (
          <div key={note.id} className="flex gap-2 text-[13px]">
            <span className="text-[10px] text-[var(--text-tertiary)] font-mono shrink-0 pt-0.5">{note.timestamp}</span>
            <span className="font-medium shrink-0 text-[var(--text-secondary)]">{note.speaker}</span>
            <span className="text-[var(--text-primary)]">{note.content}</span>
          </div>
        ))}
        <div ref={notesEndRef} />

        {/* AI suggestions */}
        {aiSuggestions.length > 0 && (
          <div className="mt-3 p-2 rounded bg-[var(--accent-bg)] border border-[var(--accent-border)]">
            <div className="flex items-center gap-1 text-[11px] font-medium text-[var(--accent)] mb-1">
              <Sparkles size={12} /> AI Analysis
            </div>
            {aiSuggestions.map((s, i) => (
              <div key={i} className="text-[11px] text-[var(--text-secondary)] flex items-start gap-1.5 py-0.5">
                {s.type === "task" && <CheckCircle2 size={11} className="mt-0.5 shrink-0 text-[var(--success)]" />}
                {s.type === "question" && <AlertCircle size={11} className="mt-0.5 shrink-0 text-[var(--warning)]" />}
                {s.type === "decision" && <FileText size={11} className="mt-0.5 shrink-0 text-[var(--accent)]" />}
                {s.content}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Input area */}
      {status === "active" && (
        <div className="px-3 py-2 border-t border-[var(--border)] bg-[var(--bg-secondary)] shrink-0">
          <div className="flex gap-1 mb-1">
            <input
              value={inputSpeaker}
              onChange={(e) => setInputSpeaker(e.target.value)}
              placeholder={"Speaker"}
              className="w-16 bg-transparent text-[11px] text-[var(--text-tertiary)] border-0 outline-none placeholder:text-[var(--text-tertiary)]/50"
            />
          </div>
          <div className="flex gap-2">
            <textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={"Type meeting notes…"}
              rows={2}
              className="flex-1 bg-[var(--bg-tertiary)] text-[12px] text-[var(--text-primary)] px-2 py-1.5 rounded border border-[var(--border)] resize-none outline-none placeholder:text-[var(--text-tertiary)]/50"
            />
            <button onClick={addNote} className="self-end p-1.5 rounded bg-[var(--brand)] text-white hover:bg-[var(--brand-hover)] transition-colors">
              <Send size={14} />
            </button>
          </div>
        </div>
      )}

      {/* End summary */}
      {status === "ended" && (
        <div className="px-3 py-3 border-t border-[var(--border)] text-[11px] text-[var(--text-secondary)] shrink-0">
          <div className="font-medium text-[var(--text-primary)] mb-1">{"Meeting ended"}</div>
          {summary ? (
            <p className="whitespace-pre-wrap mb-2">{summary}</p>
          ) : (
            <p className="text-[var(--text-tertiary)] mb-2">{"Summary unavailable."}</p>
          )}
          {createdTasks.length > 0 && (
            <ul className="space-y-1 list-disc pl-4">
              {createdTasks.map((task) => (
                <li key={task.id}>
                  {task.title}
                  {task.assignee ? ` — ${task.assignee}` : ""}
                  {task.priority ? ` (${task.priority})` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
