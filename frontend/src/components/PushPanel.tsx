import { useState, useEffect, useCallback } from "react";
import { Bell, Send, RefreshCw, Smartphone, ExternalLink } from "lucide-react";
import { Button } from "./ui";
import { toast } from "./ui/Toast";
import * as pushApi from "../api/push";
import type { PushStatus } from "../api/push";

export default function PushPanel() {
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  // editable fields
  const [enabled, setEnabled] = useState(false);
  const [provider, setProvider] = useState<"bark" | "ntfy">("bark");
  const [barkServer, setBarkServer] = useState("https://api.day.app");
  const [barkKey, setBarkKey] = useState("");
  const [ntfyServer, setNtfyServer] = useState("https://ntfy.sh");
  const [ntfyTopic, setNtfyTopic] = useState("");
  const [deepLink, setDeepLink] = useState("");
  const [notifyTaskDone, setNotifyTaskDone] = useState(true);
  const [notifyTaskWaiting, setNotifyTaskWaiting] = useState(true);
  const [notifyScheduleResult, setNotifyScheduleResult] = useState(true);
  const [notifyEmailSummary, setNotifyEmailSummary] = useState(false);

  const applyStatus = (s: PushStatus) => {
    setStatus(s);
    setEnabled(s.enabled);
    setProvider(s.provider);
    setBarkServer(s.config.bark_server || "https://api.day.app");
    setBarkKey(s.config.bark_device_key || "");
    setNtfyServer(s.config.ntfy_server || "https://ntfy.sh");
    setNtfyTopic(s.config.ntfy_topic || "");
    setDeepLink(s.config.deep_link_base || "");
    setNotifyTaskDone(s.notify_task_done);
    setNotifyTaskWaiting(s.notify_task_waiting);
    setNotifyScheduleResult(s.notify_schedule_result);
    setNotifyEmailSummary(s.notify_email_summary);
  };

  const fetchStatus = useCallback(async () => {
    try {
      const s = await pushApi.getStatus();
      applyStatus(s);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await pushApi.updateConfig({
        enabled,
        provider,
        bark_server: barkServer,
        bark_device_key: barkKey,
        ntfy_server: ntfyServer,
        ntfy_topic: ntfyTopic,
        deep_link_base: deepLink,
        notify_task_done: notifyTaskDone,
        notify_task_waiting: notifyTaskWaiting,
        notify_schedule_result: notifyScheduleResult,
        notify_email_summary: notifyEmailSummary,
      });
      toast.success("Push settings saved");
      await fetchStatus();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const r = await pushApi.sendTest();
      if (r.success) {
        toast.success("Test notification sent. Check your phone");
      } else {
        toast.error("Failed to send: check that the channel is enabled and Key/Topic are filled in");
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to send");
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <RefreshCw size={16} className="animate-spin text-[var(--text-tertiary)]" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Status card */}
      <div className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl p-5">
        <div className="flex items-center gap-2 mb-4">
          <Bell size={16} className="text-[var(--brand)]" />
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Mobile push (Bark / ntfy)</h3>
          <span
            className={`ml-auto text-xs px-2 py-0.5 rounded-full ${
              enabled && status?.configured
                ? "bg-green-500/15 text-green-500"
                : "bg-[var(--bg-primary)] text-[var(--text-tertiary)]"
            }`}
          >
            {enabled && status?.configured ? "Enabled" : "Disabled"}
          </span>
        </div>

        <label className="flex items-center justify-between py-2 cursor-pointer">
          <span className="text-sm text-[var(--text-secondary)]">Enable push channel</span>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="accent-[var(--brand)]" />
        </label>

        <div className="flex gap-2 mt-2">
          {(["bark", "ntfy"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setProvider(p)}
              className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                provider === p
                  ? "border-[var(--brand)] text-[var(--brand)] bg-[var(--brand)]/10"
                  : "border-[var(--border)] text-[var(--text-secondary)]"
              }`}
            >
              {p === "bark" ? "Bark (recommended for iOS)" : "ntfy (Android / self-hosted)"}
            </button>
          ))}
        </div>

        {provider === "bark" ? (
          <div className="mt-4 space-y-3">
            <div>
              <label className="text-xs text-[var(--text-tertiary)]">Bark server</label>
              <input
                value={barkServer}
                onChange={(e) => setBarkServer(e.target.value)}
                placeholder="https://api.day.app"
                className="w-full mt-1 px-3 py-2 text-sm bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg focus:outline-none focus:border-[var(--brand)]"
              />
            </div>
            <div>
              <label className="text-xs text-[var(--text-tertiary)]">Device key (copy the key part of the push URL in the Bark app)</label>
              <input
                value={barkKey}
                onChange={(e) => setBarkKey(e.target.value)}
                type="password"
                placeholder="e.g. aBCdEfGhIjKlMnOpQrStUv"
                className="w-full mt-1 px-3 py-2 text-sm bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg focus:outline-none focus:border-[var(--brand)] font-mono"
              />
            </div>
            <a
              href="https://apps.apple.com/app/bark-customed-notifications/id1403753865"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-[var(--brand)] hover:underline"
            >
              <Smartphone size={12} /> No Bark? Download it free from the App Store <ExternalLink size={10} />
            </a>
          </div>
        ) : (
          <div className="mt-4 space-y-3">
            <div>
              <label className="text-xs text-[var(--text-tertiary)]">ntfy server</label>
              <input
                value={ntfyServer}
                onChange={(e) => setNtfyServer(e.target.value)}
                placeholder="https://ntfy.sh"
                className="w-full mt-1 px-3 py-2 text-sm bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg focus:outline-none focus:border-[var(--brand)]"
              />
            </div>
            <div>
              <label className="text-xs text-[var(--text-tertiary)]">Topic (use a sufficiently random name on public instances)</label>
              <input
                value={ntfyTopic}
                onChange={(e) => setNtfyTopic(e.target.value)}
                type="password"
                placeholder="e.g. crabagent-x7k2p9"
                className="w-full mt-1 px-3 py-2 text-sm bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg focus:outline-none focus:border-[var(--brand)] font-mono"
              />
            </div>
          </div>
        )}

        <div className="mt-4">
          <label className="text-xs text-[var(--text-tertiary)]">
            Deep-link address (the UI address a notification opens; optional. The mobile console appends #/m/approve/… to jump straight to the approval card)
          </label>
          <input
            value={deepLink}
            onChange={(e) => setDeepLink(e.target.value)}
            placeholder="http://<device-address>:<port>"
            className="w-full mt-1 px-3 py-2 text-sm bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg focus:outline-none focus:border-[var(--brand)]"
          />
        </div>

        <div className="mt-4 pt-3 border-t border-[var(--border)] space-y-2">
          <label className="flex items-center justify-between py-1 cursor-pointer text-sm text-[var(--text-secondary)]">
            <span>Push when a task completes or fails</span>
            <input type="checkbox" checked={notifyTaskDone} onChange={(e) => setNotifyTaskDone(e.target.checked)} className="accent-[var(--brand)]" />
          </label>
          <label className="flex items-center justify-between py-1 cursor-pointer text-sm text-[var(--text-secondary)]">
            <span>Push when a task is waiting for authorization (time-sensitive, breaks through Focus)</span>
            <input type="checkbox" checked={notifyTaskWaiting} onChange={(e) => setNotifyTaskWaiting(e.target.checked)} className="accent-[var(--brand)]" />
          </label>
          <label className="flex items-center justify-between py-1 cursor-pointer text-sm text-[var(--text-secondary)]">
            <span>Push scheduled task results</span>
            <input type="checkbox" checked={notifyScheduleResult} onChange={(e) => setNotifyScheduleResult(e.target.checked)} className="accent-[var(--brand)]" />
          </label>
          <label className="flex items-center justify-between py-1 cursor-pointer text-sm text-[var(--text-secondary)]">
            <span>Email digest push</span>
            <input type="checkbox" checked={notifyEmailSummary} onChange={(e) => setNotifyEmailSummary(e.target.checked)} className="accent-[var(--brand)]" />
          </label>
        </div>

        <div className="mt-5 flex gap-2">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save settings"}
          </Button>
          <Button variant="secondary" onClick={handleTest} disabled={testing || !enabled}>
            <Send size={13} className="mr-1" />
            {testing ? "Sending…" : "Send test notification"}
          </Button>
        </div>
        <p className="mt-3 text-xs text-[var(--text-tertiary)] leading-relaxed">
          iOS users should pick Bark: install the app, open it and copy the push URL, then paste its key above.
          Tapping a notification opens the deep-link address above and returns you straight to the agent UI.
        </p>
      </div>
    </div>
  );
}
