import { useEffect, useRef, useState } from "react";
import { getTimeline, type SessionTimeline } from "../api/timeline";

const LIVE_INTERVAL_MS = 3000;
const RETRY_INTERVAL_MS = 6000;

/**
 * A chat's agent timeline. Loads when ``enabled``; while agents are working
 * (the server says so, or ``live`` is true because the chat is busy) it
 * refreshes every few seconds, and stops once everything has finished.
 */
export function useSessionTimeline(sessionId: string | null, enabled: boolean, live: boolean) {
  const [timeline, setTimeline] = useState<SessionTimeline | null>(null);
  const [error, setError] = useState("");
  const liveRef = useRef(live);
  liveRef.current = live;

  useEffect(() => {
    setTimeline((current) => (current && current.session_id === sessionId ? current : null));
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || !enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const next = await getTimeline(sessionId);
        if (cancelled) return;
        setTimeline(next);
        setError("");
        if (next.running || liveRef.current) timer = setTimeout(load, LIVE_INTERVAL_MS);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        timer = setTimeout(load, RETRY_INTERVAL_MS);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // `live` restarts the loop: polling begins when the chat gets busy and one
    // last load picks up the final state when it goes idle.
  }, [sessionId, enabled, live]);

  return { timeline, error };
}
