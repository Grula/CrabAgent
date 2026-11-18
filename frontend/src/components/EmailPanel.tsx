import { useState, useEffect } from "react";
import { X as XIcon, Mail, Loader2 } from "lucide-react";
import * as emailApi from "../api/email";
import { Button, Input } from "./ui";
import { toast } from "./ui/Toast";

interface Props {
  onClose: () => void;
}

export default function EmailPanel({ onClose }: Props) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [config, setConfig] = useState({
    imap_host: "",
    imap_port: 993,
    imap_user: "",
    imap_pass: "",
    smtp_host: "",
    smtp_port: 587,
    smtp_user: "",
    smtp_pass: "",
    check_interval: 300,
    enabled: false,
  });

  useEffect(() => {
    emailApi.getEmailConfig().then((data) => {
      if (data && Object.keys(data).length > 0) {
        setConfig((prev) => ({ ...prev, ...data }));
      }
    }).finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const result = await emailApi.saveEmailConfig(config);
      setConfig((prev) => ({ ...prev, ...result }));
      toast.success("Email configuration saved");
    } catch {
      toast.error("Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const result = await emailApi.testEmailConnection();
      toast.success(result.result);
    } catch {
      toast.error("Connection test failed");
    } finally {
      setTesting(false);
    }
  };

  const update = (field: string, value: unknown) => {
    setConfig((prev) => ({ ...prev, [field]: value }));
  };

  if (loading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
        <div className="p-8 rounded-xl bg-[var(--bg-primary)] text-sm text-[var(--text-tertiary)]">
          {"Loading…"}
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div className="w-full max-w-lg max-h-[90vh] rounded-xl bg-[var(--bg-primary)] border border-[var(--border)] shadow-[var(--shadow-xl)] flex flex-col animate-scale-in overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
          <div className="flex items-center gap-2">
            <Mail size={16} className="text-[var(--accent)]" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">
              {"Email Configuration"}
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <XIcon size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* IMAP Section */}
          <div>
            <h3 className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide mb-2">
              {"INCOMING (IMAP)"}
            </h3>
            <div className="space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <div className="col-span-2">
                  <Input
                    placeholder={"imap.example.com"}
                    value={config.imap_host}
                    onChange={(e) => update("imap_host", e.target.value)}
                  />
                </div>
                <Input
                  type="number"
                  placeholder="993"
                  value={String(config.imap_port)}
                  onChange={(e) => update("imap_port", parseInt(e.target.value) || 993)}
                />
              </div>
              <Input
                placeholder={"Email address"}
                value={config.imap_user}
                onChange={(e) => update("imap_user", e.target.value)}
              />
              <Input
                type="password"
                placeholder={"Password / App Password"}
                value={config.imap_pass}
                onChange={(e) => update("imap_pass", e.target.value)}
              />
            </div>
          </div>

          {/* SMTP Section */}
          <div>
            <h3 className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide mb-2">
              {"OUTGOING (SMTP)"}
            </h3>
            <div className="space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <div className="col-span-2">
                  <Input
                    placeholder={"smtp.example.com"}
                    value={config.smtp_host}
                    onChange={(e) => update("smtp_host", e.target.value)}
                  />
                </div>
                <Input
                  type="number"
                  placeholder="587"
                  value={String(config.smtp_port)}
                  onChange={(e) => update("smtp_port", parseInt(e.target.value) || 587)}
                />
              </div>
              <Input
                placeholder={"Email address"}
                value={config.smtp_user}
                onChange={(e) => update("smtp_user", e.target.value)}
              />
              <Input
                type="password"
                placeholder={"Password / App Password"}
                value={config.smtp_pass}
                onChange={(e) => update("smtp_pass", e.target.value)}
              />
            </div>
          </div>

          {/* Options */}
          <div>
            <h3 className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide mb-2">
              {"OPTIONS"}
            </h3>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="email_enabled"
                  checked={config.enabled}
                  onChange={(e) => update("enabled", e.target.checked)}
                  className="cursor-pointer accent-[var(--accent)]"
                />
                <label htmlFor="email_enabled" className="text-xs text-[var(--text-secondary)] cursor-pointer">
                  {"Enable email integration"}
                </label>
              </div>
              <div className="grid grid-cols-3 gap-2 items-center">
                <label className="text-xs text-[var(--text-secondary)]">
                  {"Check interval (s)"}
                </label>
                <Input
                  type="number"
                  value={String(config.check_interval)}
                  onChange={(e) => update("check_interval", parseInt(e.target.value) || 300)}
                />
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-[var(--border)] bg-[var(--bg-secondary)]/50">
          <Button
            variant="ghost"
            onClick={handleTest}
            disabled={testing || !config.imap_host}
          >
            {testing ? <Loader2 size={14} className="animate-spin" /> : null}
            {testing ? "Testing…" : "Test Connection"}
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>
              {"Cancel"}
            </Button>
            <Button variant="brand" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
