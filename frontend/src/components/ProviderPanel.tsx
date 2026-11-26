import { useState, useEffect, useRef } from "react";
import { Plus, Trash2, Star, StarOff, ChevronDown, ChevronRight, X, Check, ExternalLink, Loader2 } from "lucide-react";
import { Provider, CatalogEntry } from "../api/providers";
import * as providersApi from "../api/providers";
import * as chatgptApi from "../api/chatgpt";

/** Format a duration in seconds to a human-readable window label. */
function formatWindow(seconds: number | null | undefined): string {
  if (!seconds) return "";
  if (seconds >= 86400) return `${Math.round(seconds / 86400)}d window`;
  if (seconds >= 3600) return `${Math.round(seconds / 3600)}h window`;
  return `${Math.round(seconds / 60)}min window`;
}

/** Format remaining time before reset. */
function formatReset(seconds: number | null | undefined): string {
  if (!seconds) return "";
  if (seconds >= 86400) return `${(seconds / 86400).toFixed(1)}d until reset`;
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)}h until reset`;
  return `${Math.round(seconds / 60)}min until reset`;
}

function providerLabel(provider: Provider): string {
  const displayName = provider.display_name || provider.name;
  return displayName === provider.name ? provider.name : `${displayName} (${provider.name})`;
}

function isZhipuCodingPlan(provider: Provider): boolean {
  return provider.base_url.includes("/api/coding/");
}
import * as quotaApi from "../api/quota";
import { formatDate, parseDateTime } from "../api/time";
import { Modal, Button, Input, PasswordInput, ConfirmDialog, EmptyState } from "./ui";
import { toast } from "./ui/Toast";
import { cn } from "../lib/cn";
import { emitProvidersChanged } from "../lib/providerSync";

interface Props {
  providers: Provider[];
  catalog: CatalogEntry[];
  onClose: () => void;
  onRefresh: () => void;
}

export default function ProviderPanel({
  providers,
  catalog,
  onClose,
  onRefresh,
}: Props) {
  const [mode, setMode] = useState<"list" | "add">("list");
  const [formType, setFormType] = useState(catalog[0]?.type || "");
  const [formVariantId, setFormVariantId] = useState<string>("");
  const [formName, setFormName] = useState("");
  const [formKey, setFormKey] = useState("");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [newModelInput, setNewModelInput] = useState<Record<string, string>>({});
  const [modelBusy, setModelBusy] = useState<string | null>(null);
  const [proxyInput, setProxyInput] = useState<Record<string, string>>({});
  const [proxyBusy, setProxyBusy] = useState<string | null>(null);
  const [chatgptAuth, setChatgptAuth] = useState<chatgptApi.ChatGPTAuthStatus | null>(null);
  const [chatgptDeviceCode, setChatgptDeviceCode] = useState<chatgptApi.DeviceCodeInfo | null>(null);
  const [chatgptBusy, setChatgptBusy] = useState(false);
  const [chatgptPolling, setChatgptPolling] = useState(false);
  const [chatgptAccount, setChatgptAccount] = useState<chatgptApi.ChatGPTAccountInfo | null>(null);
  const [chatgptAccountBusy, setChatgptAccountBusy] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetConfirmTarget, setResetConfirmTarget] = useState(false);
  const [deepseekQuotas, setDeepseekQuotas] = useState<Record<string, quotaApi.ProviderQuota>>({});
  const [deepseekBusy, setDeepseekBusy] = useState<string | null>(null);
  const [zhipuQuotas, setZhipuQuotas] = useState<Record<string, quotaApi.ProviderQuota>>({});
  const [zhipuBusy, setZhipuBusy] = useState<string | null>(null);
  const [opencodeQuotas, setOpencodeQuotas] = useState<Record<string, quotaApi.OpenCodeGoQuota>>({});
  const [opencodeBusy, setOpencodeBusy] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Check ChatGPT auth status when a chatgpt provider exists
  const chatgptProvider = providers.find((p) => p.type === "chatgpt");
  useEffect(() => {
    if (chatgptProvider) {
      chatgptApi.getAuthStatus().then(setChatgptAuth).catch(() => {});
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [chatgptProvider?.name]);

  const handleChatgptLogin = async () => {
    setChatgptBusy(true);
    try {
      const code = await chatgptApi.startDeviceCode();
      setChatgptDeviceCode(code);
      // Start polling
      setChatgptPolling(true);
      const poll = async () => {
        try {
          const status = await chatgptApi.pollDeviceAuth(code.device_auth_id, code.user_code);
          if (status.authenticated) {
            setChatgptAuth(status);
            setChatgptPolling(false);
            setChatgptDeviceCode(null);
            if (pollRef.current) clearInterval(pollRef.current);
            toast.success("ChatGPT login successful");
          }
        } catch {
          // Ignore polling errors
        }
      };
      pollRef.current = setInterval(poll, (code.interval || 5) * 1000);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to start login");
    } finally {
      setChatgptBusy(false);
    }
  };

  const handleChatgptLogout = async () => {
    try {
      await chatgptApi.logout();
      setChatgptAuth({ authenticated: false });
      setChatgptAccount(null);
      toast.success("Signed out of ChatGPT");
    } catch {
      toast.error("Sign-out failed");
    }
  };

  const handleChatgptAccountInfo = async () => {
    setChatgptAccountBusy(true);
    try {
      const info = await chatgptApi.getAccountInfo();
      setChatgptAccount(info);
    } catch {
      toast.error("Failed to fetch account info");
    } finally {
      setChatgptAccountBusy(false);
    }
  };

  const handleConsumeReset = () => {
    const credits = chatgptAccount?.rate_limits?.banked_resets;
    const available = credits?.available_count ?? 0;
    if (available <= 0) {
      toast.error("No reset credits available");
      return;
    }
    setResetConfirmTarget(true);
  };

  const handleConfirmReset = async () => {
    const credits = chatgptAccount?.rate_limits?.banked_resets;
    const firstCredit = credits?.credits?.[0];
    const creditId = firstCredit?.id;
    if (!creditId) {
      toast.error("Could not get the reset credit ID");
      return;
    }
    setResetConfirmTarget(false);
    setResetBusy(true);
    try {
      await chatgptApi.consumeResetCredit(creditId);
      toast.success("Quota reset!");
      // Refresh account info after reset
      const info = await chatgptApi.getAccountInfo();
      setChatgptAccount(info);
    } catch {
      toast.error("Reset failed. Try again later");
    } finally {
      setResetBusy(false);
    }
  };

  const handleDeepSeekQuota = async (name: string) => {
    setDeepseekBusy(name);
    try {
      const info = await quotaApi.getProviderQuota(name);
      setDeepseekQuotas((current) => ({ ...current, [name]: info }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to fetch DeepSeek balance");
    } finally {
      setDeepseekBusy(null);
    }
  };

  const handleOpenCodeQuota = async (name: string) => {
    setOpencodeBusy(name);
    try {
      const info = await quotaApi.getProviderQuota(name);
      setOpencodeQuotas((current) => ({ ...current, [name]: info.summary as quotaApi.OpenCodeGoQuota }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to fetch OpenCode Go quota");
    } finally {
      setOpencodeBusy(null);
    }
  };

  const handleZhipuQuota = async (name: string) => {
    setZhipuBusy(name);
    try {
      const info = await quotaApi.getProviderQuota(name);
      setZhipuQuotas((current) => ({ ...current, [name]: info }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to fetch Zhipu quota");
    } finally {
      setZhipuBusy(null);
    }
  };

  const handleAddExtraModel = async (providerName: string) => {
    const modelId = (newModelInput[providerName] || "").trim();
    if (!modelId) return;
    const provider = providers.find((p) => p.name === providerName);
    if (!provider) return;
    const current = provider.extra_models || [];
    if (current.includes(modelId)) {
      toast.error("Model already exists");
      return;
    }
    setModelBusy(providerName);
    try {
      await providersApi.updateProvider(providerName, {
        extra_models: [...current, modelId],
      });
      toast.success(`Added model: ${modelId}`);
      emitProvidersChanged();
      onRefresh();
      setNewModelInput({ ...newModelInput, [providerName]: "" });
    } catch {
      toast.error("Failed to add model");
    } finally {
      setModelBusy(null);
    }
  };

  const handleRemoveExtraModel = async (providerName: string, modelId: string) => {
    const provider = providers.find((p) => p.name === providerName);
    if (!provider) return;
    const current = provider.extra_models || [];
    setModelBusy(`${providerName}:${modelId}`);
    try {
      await providersApi.updateProvider(providerName, {
        extra_models: current.filter((m) => m !== modelId),
      });
      toast.success(`Removed model: ${modelId}`);
      emitProvidersChanged();
      onRefresh();
    } catch {
      toast.error("Failed to remove model");
    } finally {
      setModelBusy(null);
    }
  };

  const handleToggleProxy = async (providerName: string) => {
    const provider = providers.find((p) => p.name === providerName);
    if (!provider) return;
    setProxyBusy(providerName);
    try {
      await providersApi.updateProvider(providerName, {
        proxy_enabled: !provider.proxy_enabled,
      });
      emitProvidersChanged();
      onRefresh();
    } catch {
      toast.error("Failed to toggle proxy");
    } finally {
      setProxyBusy(null);
    }
  };

  const handleSaveProxyUrl = async (providerName: string) => {
    const url = (proxyInput[providerName] ?? "").trim();
    const provider = providers.find((p) => p.name === providerName);
    if (!provider) return;
    // Skip if unchanged
    if (url === (provider.proxy_url ?? "")) return;
    setProxyBusy(`${providerName}:url`);
    try {
      await providersApi.updateProvider(providerName, { proxy_url: url });
      toast.success("Proxy URL saved");
      emitProvidersChanged();
      onRefresh();
    } catch {
      toast.error("Failed to save proxy URL");
    } finally {
      setProxyBusy(null);
    }
  };

  const handleAdd = async () => {
    setError(null);
    if (!formName || !formType) {
      setError("Name and type are required");
      return;
    }
    // OAuth-based providers don't need api_key, and neither do local/custom
    // endpoints (the catalog marks those with api_key_required: false).
    const isOAuth = selectedCatalog?.auth_type === "oauth";
    const keyRequired = !isOAuth && selectedCatalog?.api_key_required !== false;
    if (needsCustomBaseUrl && !formBaseUrl.trim()) {
      setError("Base URL is required");
      return;
    }
    if (keyRequired && !formKey) {
      setError("API key is required");
      return;
    }
    setBusy(true);
    try {
      await providersApi.createProvider({
        name: formName,
        display_name: formName,
        type: formType,
        api_key: formKey,
        base_url: needsCustomBaseUrl ? formBaseUrl.trim() : undefined,
        variant_id: formVariantId || undefined,
      });
      toast.success("Provider added");
      emitProvidersChanged();
      onRefresh();
      setMode("list");
      setFormName("");
      setFormKey("");
      setFormVariantId("");
      setFormBaseUrl("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to add provider");
    } finally {
      setBusy(false);
    }
  };

  const handleSetDefault = async (name: string) => {
    try {
      await providersApi.updateProvider(name, { is_default: true });
      toast.success("Default provider updated");
      emitProvidersChanged();
      onRefresh();
    } catch {
      toast.error("Failed to set default");
    }
  };

  const handleDelete = async (name: string) => {
    try {
      await providersApi.deleteProvider(name);
      toast.success("Provider deleted");
      emitProvidersChanged();
      onRefresh();
    } catch {
      toast.error("Failed to delete provider");
    } finally {
      setDeleteTarget(null);
    }
  };

  const selectedCatalog = catalog.find((c) => c.type === formType);
  const selectedVariant = selectedCatalog?.variants.find((v) => v.id === formVariantId);
  const displayBaseUrl = selectedVariant?.base_url || selectedCatalog?.base_url;
  // A catalog entry with no fixed base_url (local / custom endpoint) needs the
  // user to type one; OAuth entries have no base URL at all.
  const needsCustomBaseUrl = !!selectedCatalog && !selectedCatalog.base_url && selectedCatalog.auth_type !== "oauth";

  return (
    <>
      <Modal
        open={true}
        onOpenChange={(o) => !o && onClose()}
        title={mode === "list" ? "LLM Providers" : "Add Provider"}
        description={
          mode === "list"
            ? "Manage your LLM provider credentials"
            : "Configure a new provider connection"
        }
        size="md"
        footer={
          mode === "add" ? (
            <>
              <Button variant="ghost" onClick={() => setMode("list")}>
                {"Cancel"}
              </Button>
              <Button variant="brand" loading={busy} onClick={handleAdd}>
                {"Add Provider"}
              </Button>
            </>
          ) : (
            <Button variant="brand" onClick={() => setMode("add")}>
              <Plus size={14} /> {"Add Provider"}
            </Button>
          )
        }
      >
        {mode === "list" ? (
          <div className="space-y-2">
            {providers.length === 0 ? (
              <EmptyState
                title={"No providers configured"}
                description={"Add an LLM provider to start using CrabAgent."}
                action={
                  <Button variant="brand" size="sm" onClick={() => setMode("add")}>
                    <Plus size={14} /> {"Add Provider"}
                  </Button>
                }
              />
            ) : (
              providers.map((p) => (
                <div
                  key={p.name}
                  className={cn(
                    "rounded-xl border border-[var(--border)]",
                    "bg-[var(--bg-tertiary)] overflow-hidden",
                  )}
                >
                  <div className="p-3 flex items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-[var(--text-primary)] flex items-center gap-1.5">
                        <span className="truncate">{providerLabel(p)}</span>
                        {p.is_default && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--brand-bg)] text-[var(--brand)] border border-[var(--brand-border)] flex items-center gap-1 shrink-0">
                            <Star size={9} /> default
                          </span>
                        )}
                        {(p.extra_models?.length ?? 0) > 0 && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-secondary)] text-[var(--text-tertiary)] border border-[var(--border)] shrink-0">
                            +{p.extra_models!.length} models
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-[var(--text-tertiary)] font-mono truncate">
                        {p.type} · {p.api_key_preview}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setExpandedProvider(expandedProvider === p.name ? null : p.name)}
                        title="Extra models"
                      >
                        {expandedProvider === p.name ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </Button>
                      {!p.is_default && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleSetDefault(p.name)}
                          title={"Set as default"}
                        >
                          <StarOff size={14} />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteTarget(p.name)}
                        title={"Delete"}
                        className="text-[var(--danger)] hover:text-[var(--danger)] hover:bg-[var(--danger-bg)]"
                      >
                        <Trash2 size={14} />
                      </Button>
                    </div>
                  </div>
                  {expandedProvider === p.name && (
                    <div className="px-3 pb-3 pt-1 border-t border-[var(--border)] space-y-3">
                      {/* ChatGPT OAuth Section */}
                      {p.type === "chatgpt" && (
                        <div className="pt-1.5 space-y-2">
                          <div className="text-[11px] font-medium text-[var(--text-secondary)] flex items-center gap-1.5">
                            ChatGPT subscription sign-in
                            {chatgptAuth?.authenticated ? (
                              <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-600 border border-green-500/20">
                                <Check size={9} /> Connected
                              </span>
                            ) : (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--danger-bg)] text-[var(--danger)] border border-[var(--danger-border)]">
                                Not signed in
                              </span>
                            )}
                          </div>

                          {/* Device Code Login UI */}
                          {chatgptDeviceCode && chatgptPolling && (
                            <div className="p-3 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] space-y-2">
                              <p className="text-xs text-[var(--text-primary)]">
                                1. Open the link below and sign in to your ChatGPT account
                              </p>
                              <a
                                href={chatgptDeviceCode.verification_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 text-xs text-[var(--brand)] hover:underline"
                              >
                                {chatgptDeviceCode.verification_url}
                                <ExternalLink size={10} />
                              </a>
                              <p className="text-xs text-[var(--text-primary)]">2. Enter this authorization code:</p>
                              <div className="flex items-center gap-2">
                                <code className="px-3 py-1.5 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] text-sm font-mono tracking-wider text-[var(--brand)]">
                                  {chatgptDeviceCode.user_code}
                                </code>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => {
                                    navigator.clipboard.writeText(chatgptDeviceCode.user_code);
                                    toast.success("Copied to clipboard");
                                  }}
                                >
                                  Copy
                                </Button>
                              </div>
                              <div className="flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)]">
                                <Loader2 size={10} className="animate-spin" />
                                Waiting for authorization...
                              </div>
                            </div>
                          )}

                          {/* Auth Buttons */}
                          <div className="flex items-center gap-2">
                            {chatgptAuth?.authenticated ? (
                              <>
                                <Button variant="secondary" size="sm" onClick={handleChatgptAccountInfo} loading={chatgptAccountBusy}>
                                  {chatgptAccountBusy ? "Querying..." : "View quota"}
                                </Button>
                                <Button variant="ghost" size="sm" onClick={handleChatgptLogout} className="text-[var(--danger)]">
                                  Sign out
                                </Button>
                              </>
                            ) : (
                              <Button variant="brand" size="sm" onClick={handleChatgptLogin} loading={chatgptBusy}>
                                Sign in to ChatGPT
                              </Button>
                            )}
                          </div>

                          {/* Account Info Display */}
                          {chatgptAccount && (
                            <div className="p-3 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-[11px] space-y-2">
                              {/* Basic info */}
                              <div className="flex justify-between">
                                <span className="text-[var(--text-tertiary)]">Email</span>
                                <span className="text-[var(--text-primary)]">{chatgptAccount.email || "—"}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-[var(--text-tertiary)]">Subscription</span>
                                <span className="px-1.5 py-0.5 rounded bg-[var(--brand-bg)] text-[var(--brand)] border border-[var(--brand-border)] font-medium uppercase text-[10px]">
                                  {chatgptAccount.plan || "?"}
                                </span>
                              </div>

                              {/* Real-time usage */}
                              {chatgptAccount.rate_limits && !chatgptAccount.rate_limits.error && (
                                <div className="pt-2 border-t border-[var(--border)] space-y-2.5">
                                  <div className="text-[var(--text-secondary)] font-medium flex items-center gap-1.5">
                                    Live quota
                                    {chatgptAccount.rate_limits.active_limit && (
                                      <span className="text-[10px] px-1 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]">
                                        {chatgptAccount.rate_limits.active_limit}
                                      </span>
                                    )}
                                  </div>

                                  {/* Primary window — label and format are derived from raw seconds */}
                                  {chatgptAccount.rate_limits.primary && (
                                    <UsageBar
                                      label={formatWindow(chatgptAccount.rate_limits.primary.window_seconds) || "Usage window"}
                                      usedPercent={chatgptAccount.rate_limits.primary.used_percent}
                                      windowText={
                                        chatgptAccount.rate_limits.primary.window_seconds
                                          ? formatWindow(chatgptAccount.rate_limits.primary.window_seconds).replace("window", "rolling window")
                                          : ""
                                      }
                                      resetText={formatReset(chatgptAccount.rate_limits.primary.reset_after_seconds)}
                                    />
                                  )}

                                  {/* Secondary window — only rendered when API returns one */}
                                  {chatgptAccount.rate_limits.secondary && (
                                    <UsageBar
                                      label={formatWindow(chatgptAccount.rate_limits.secondary.window_seconds) || "Secondary window"}
                                      usedPercent={chatgptAccount.rate_limits.secondary.used_percent}
                                      windowText={
                                        chatgptAccount.rate_limits.secondary.window_seconds
                                          ? formatWindow(chatgptAccount.rate_limits.secondary.window_seconds).replace("window", "rolling window")
                                          : ""
                                      }
                                      resetText={formatReset(chatgptAccount.rate_limits.secondary.reset_after_seconds)}
                                    />
                                  )}

                                  {/* Credits */}
                                  {chatgptAccount.rate_limits.credits && (
                                    <div className="flex justify-between text-[10px] text-[var(--text-tertiary)]">
                                      <span>Credits</span>
                                      <span>
                                        {chatgptAccount.rate_limits.credits.unlimited ? "Unlimited" :
                                         chatgptAccount.rate_limits.credits.has_credits ? `Balance: ${chatgptAccount.rate_limits.credits.balance || "—"}` :
                                         "No extra credits"}
                                      </span>
                                    </div>
                                  )}

                                  {/* Banked Rate-Limit Resets */}
                                  {chatgptAccount.rate_limits.banked_resets && (
                                    <div className="pt-2 border-t border-[var(--border)] space-y-1.5">
                                      <div className="flex items-center justify-between">
                                        <span className="text-[var(--text-secondary)] font-medium">Manual quota reset</span>
                                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${chatgptAccount.rate_limits.banked_resets.available_count && chatgptAccount.rate_limits.banked_resets.available_count > 0 ? "bg-[var(--success-bg)] text-[var(--success)]" : "bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]"}`}>
                                          {chatgptAccount.rate_limits.banked_resets.available_count ?? 0} available
                                        </span>
                                      </div>
                                      {chatgptAccount.rate_limits.banked_resets.credits && chatgptAccount.rate_limits.banked_resets.credits.length > 0 && (
                                        <div className="space-y-1">
                                          {chatgptAccount.rate_limits.banked_resets.credits.map((c, idx) => (
                                            <div key={c.id || idx} className="flex justify-between text-[10px] text-[var(--text-tertiary)]">
                                              <span>{c.reset_type === "codex_rate_limits" ? "Codex rate-limit reset" : c.reset_type || "Rate-limit reset"}</span>
                                              {c.expires_at && <span>Expires: {formatDate(c.expires_at)}</span>}
                                            </div>
                                          ))}
                                        </div>
                                      )}
                                      {(chatgptAccount.rate_limits.banked_resets.available_count ?? 0) > 0 && (
                                        <Button
                                          variant="brand"
                                          size="sm"
                                          onClick={handleConsumeReset}
                                          loading={resetBusy}
                                          className="w-full mt-1"
                                        >
                                          {resetBusy ? "Resetting..." : "⚡ Use reset now"}
                                        </Button>
                                      )}
                                    </div>
                                  )}
                                </div>
                              )}

                              {chatgptAccount.rate_limits?.error && (
                                <div className="pt-2 border-t border-[var(--border)] text-[var(--danger)]">
                                  {chatgptAccount.rate_limits.error}
                                </div>
                              )}

                              {!chatgptAccount.email && !chatgptAccount.plan && (
                                <div className="text-[var(--text-tertiary)]">Could not fetch account info. Please sign in again</div>
                              )}
                            </div>
                          )}
                        </div>
                      )}

                      {p.type === "opencode-go" && (
                        <div className="pt-1.5 space-y-2">
                          <Button variant="secondary" size="sm" onClick={() => handleOpenCodeQuota(p.name)} loading={opencodeBusy === p.name}>
                            {opencodeBusy === p.name ? "Querying..." : "View quota"}
                          </Button>
                          {opencodeQuotas[p.name] && (
                            <div className="p-3 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-[11px] space-y-3">
                              {([["rolling", "5-hour window"], ["weekly", "Weekly"], ["monthly", "Monthly"]] as const).map(([key, label]) => {
                                const window = opencodeQuotas[p.name][key];
                                const reset = parseDateTime(window.resetsAt)?.getTime() ?? NaN;
                                return <UsageBar key={key} label={`${label} · used`} usedPercent={window.percent}
                                  windowText={`Remaining ${Math.max(0, 100 - window.percent).toFixed(1)}%`}
                                  resetText={Number.isFinite(reset) ? `${new Date(reset).toLocaleString()} · reset` : ""} />;
                              })}
                            </div>
                          )}
                        </div>
                      )}

                      {/* DeepSeek Quota Section */}
                      {p.type === "deepseek" && (
                        <div className="pt-1.5 space-y-2">
                          <div className="flex items-center gap-2">
                            <Button variant="secondary" size="sm" onClick={() => handleDeepSeekQuota(p.name)} loading={deepseekBusy === p.name}>
                              {deepseekBusy === p.name ? "Querying..." : "View balance"}
                            </Button>
                          </div>
                          {deepseekQuotas[p.name] && (
                            <div className="p-3 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-[11px] space-y-2">
                              <div className="flex justify-between">
                                <span className="text-[var(--text-tertiary)]">Available</span>
                                <span className={(deepseekQuotas[p.name].summary as quotaApi.DeepSeekQuota).is_available ? "text-green-500" : "text-[var(--danger)]"}>
                                  {(deepseekQuotas[p.name].summary as quotaApi.DeepSeekQuota).is_available ? "Yes" : "No"}
                                </span>
                              </div>
                              {(deepseekQuotas[p.name].summary as quotaApi.DeepSeekQuota).balances.map((b, i) => (
                                <div key={i} className="pt-1.5 border-t border-[var(--border)] space-y-1.5">
                                  <div className="text-[var(--text-secondary)] font-medium">
                                    {b.currency === "CNY" ? "💴 CNY" : "💵 USD"}
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-[var(--text-tertiary)]">Total balance</span>
                                    <span className="text-[var(--text-primary)] font-medium">{b.total}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-[var(--text-tertiary)]">Top-up balance</span>
                                    <span className="text-[var(--text-primary)]">{b.topped_up}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-[var(--text-tertiary)]">Granted balance</span>
                                    <span className="text-[var(--text-primary)]">{b.granted}</span>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Zhipu Quota Section */}
                      {p.type === "zhipu" && (
                        <div className="pt-1.5 space-y-2">
                          {isZhipuCodingPlan(p) ? (
                            <>
                              <div className="flex items-center gap-2">
                                <Button variant="secondary" size="sm" onClick={() => handleZhipuQuota(p.name)} loading={zhipuBusy === p.name}>
                                  {zhipuBusy === p.name ? "Querying..." : "View quota"}
                                </Button>
                              </div>
                              {zhipuQuotas[p.name] && (() => {
                                const quota = zhipuQuotas[p.name].summary as quotaApi.ZhipuQuota;
                                return (
                                  <div className="p-3 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-[11px] space-y-2">
                                    <div className="flex justify-between">
                                      <span className="text-[var(--text-tertiary)]">Plan tier</span>
                                      <span className="px-1.5 py-0.5 rounded bg-[var(--brand-bg)] text-[var(--brand)] border border-[var(--brand-border)] font-medium uppercase text-[10px]">
                                        {quota.level || "?"}
                                      </span>
                                    </div>
                                    {quota.token_limits.map((tl, i) => (
                                      <div key={i} className="pt-2 border-t border-[var(--border)]">
                                        <UsageBar
                                          label={tl.label === "WEEK" ? "📅 Week window" : `⏱ ${tl.number}h window`}
                                          usedPercent={tl.percentage}
                                          resetText={formatResetCountdown(tl.nextResetTime)}
                                        />
                                      </div>
                                    ))}
                                    {quota.time_limit && (
                                      <div className="pt-2 border-t border-[var(--border)] space-y-1.5">
                                        <div className="text-[var(--text-secondary)] font-medium">📊 MCP monthly quota</div>
                                        <UsageBar label="used" usedPercent={quota.time_limit.percentage} />
                                        <div className="flex justify-between text-[10px] text-[var(--text-tertiary)]">
                                          <span>Remaining: {quota.time_limit.remaining} / {quota.time_limit.usage}</span>
                                          {quota.time_limit.nextResetTime && (
                                            <span>{formatResetCountdown(quota.time_limit.nextResetTime)}</span>
                                          )}
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                );
                              })()}
                            </>
                          ) : (
                            <div className="text-[10px] text-[var(--text-tertiary)]">
                              Organization or standard open-platform API keys cannot query quota here; the Zhipu usage API only supports the personal Coding Plan.
                            </div>
                          )}
                        </div>
                      )}

                      {/* Proxy Section */}
                      <div className="pt-1.5">
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[11px] font-medium text-[var(--text-secondary)]">
                            Network proxy
                          </span>
                          <button
                            onClick={() => handleToggleProxy(p.name)}
                            disabled={proxyBusy === p.name}
                            className={cn(
                              "relative inline-flex h-4 w-7 items-center rounded-full transition-colors",
                              p.proxy_enabled ? "bg-[var(--brand)]" : "bg-[var(--bg-secondary)] border border-[var(--border)]",
                            )}
                          >
                            <span
                              className={cn(
                                "inline-block h-3 w-3 transform rounded-full bg-white transition-transform",
                                p.proxy_enabled ? "translate-x-3.5" : "translate-x-0.5",
                              )}
                            />
                          </button>
                        </div>
                        {p.proxy_enabled && (
                          <div className="flex items-center gap-1.5">
                            <input
                              type="text"
                              value={proxyInput[p.name] ?? p.proxy_url ?? ""}
                              onChange={(e) => setProxyInput({ ...proxyInput, [p.name]: e.target.value })}
                              onKeyDown={(e) => { if (e.key === "Enter") handleSaveProxyUrl(p.name); }}
                              placeholder="Proxy address (leave blank to use the global proxy)"
                              className="flex-1 h-8 px-2.5 text-xs rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)]/30"
                            />
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => handleSaveProxyUrl(p.name)}
                              loading={proxyBusy === `${p.name}:url`}
                            >
                              <Check size={12} />
                            </Button>
                          </div>
                        )}
                        <p className="text-[10px] text-[var(--text-tertiary)] mt-1">
                          When enabled, requests for this provider go through the proxy
                        </p>
                      </div>

                      {/* Extra Models Section */}
                      <div className="border-t border-[var(--border)] pt-2">
                        <div className="text-[11px] font-medium text-[var(--text-secondary)] mb-1.5">
                          Extra models (callable but not returned by the API)
                        </div>
                      <div className="flex flex-wrap gap-1.5">
                        {(p.extra_models || []).map((m) => (
                          <span
                            key={m}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-[var(--bg-secondary)] border border-[var(--border)] text-xs text-[var(--text-primary)] font-mono"
                          >
                            {m}
                            <button
                              onClick={() => handleRemoveExtraModel(p.name, m)}
                              disabled={modelBusy === `${p.name}:${m}`}
                              className="text-[var(--text-tertiary)] hover:text-[var(--danger)] transition-colors"
                            >
                              <X size={11} />
                            </button>
                          </span>
                        ))}
                        {(p.extra_models || []).length === 0 && (
                          <span className="text-[11px] text-[var(--text-tertiary)]">No extra models</span>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <input
                          type="text"
                          value={newModelInput[p.name] || ""}
                          onChange={(e) => setNewModelInput({ ...newModelInput, [p.name]: e.target.value })}
                          onKeyDown={(e) => { if (e.key === "Enter") handleAddExtraModel(p.name); }}
                          placeholder="Enter a model ID, e.g. glm-5.2"
                          className="flex-1 h-8 px-2.5 text-xs rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)]/30"
                        />
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => handleAddExtraModel(p.name)}
                          loading={modelBusy === p.name}
                        >
                          <Plus size={12} />
                        </Button>
                      </div>
                      </div>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {error && (
              <div className="px-3 py-2 rounded-lg bg-[var(--danger-bg)] border border-[var(--danger-border)] text-xs text-[var(--danger)]">
                {error}
              </div>
            )}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-[var(--text-secondary)]">
                Type
              </label>
              <select
                value={formType}
                onChange={(e) => {
                  setFormType(e.target.value);
                  setFormVariantId("");
                }}
                className="w-full h-9 px-3 text-sm rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand)]/30"
              >
                {catalog.map((c) => (
                  <option key={c.type} value={c.type}>
                    {c.display_name}
                  </option>
                ))}
              </select>
              {needsCustomBaseUrl ? (
                <Input
                  label={"Base URL"}
                  value={formBaseUrl}
                  onChange={(e) => setFormBaseUrl(e.target.value)}
                  placeholder={"http://127.0.0.1:8080/v1"}
                />
              ) : (
                selectedCatalog && (
                  <p className="text-[11px] text-[var(--text-tertiary)] font-mono">
                    Base URL: {selectedCatalog.auth_type === "oauth" ? "OAuth (no Base URL needed)" : displayBaseUrl}
                  </p>
                )
              )}
            </div>
            {selectedCatalog && selectedCatalog.variants.length > 0 && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-[var(--text-secondary)]">
                  Variant
                </label>
                <select
                  value={formVariantId}
                  onChange={(e) => setFormVariantId(e.target.value)}
                  className="w-full h-9 px-3 text-sm rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand)]/30"
                >
                  <option value="">{"Default"}</option>
                  {selectedCatalog.variants.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.display_name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <Input
              label={"Name"}
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder={"my-provider"}
            />
            {selectedCatalog?.auth_type === "oauth" ? (
              <div className="px-3 py-3 rounded-lg bg-[var(--brand-bg)] border border-[var(--brand-border)] text-xs text-[var(--text-secondary)]">
                <div className="font-medium text-[var(--text-primary)] mb-1">🔒 OAuth authentication</div>
                <p>This provider signs in with a ChatGPT subscription and needs no API key.</p>
                <p className="mt-1">After adding it, click “Sign in to ChatGPT” in the provider list to finish authorizing.</p>
                {selectedCatalog.models && selectedCatalog.models.length > 0 && (
                  <p className="mt-1 text-[var(--text-tertiary)]">
                    Available models: {selectedCatalog.models.slice(0, 4).join(", ")}
                    {selectedCatalog.models.length > 4 ? " ..." : ""}
                  </p>
                )}
              </div>
            ) : (
              <PasswordInput
                label={selectedCatalog?.api_key_required === false ? "API Key (optional)" : "API Key"}
                value={formKey}
                onChange={(e) => setFormKey(e.target.value)}
                placeholder={selectedCatalog?.api_key_required === false ? "not needed for a local server" : "sk-…"}
              />
            )}
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title={`Delete provider "${deleteTarget}"?`}
        description={"This will permanently remove the provider and its encrypted API key."}
        confirmText={"Delete"}
        tone="danger"
        onConfirm={() => { if (deleteTarget) handleDelete(deleteTarget); }}
      />

      {/* Reset credit confirmation */}
      <ConfirmDialog
        open={resetConfirmTarget}
        onOpenChange={setResetConfirmTarget}
        title="Confirm quota reset"
        description="This immediately consumes one reset card and clears usage for the current 5-hour and 7-day windows. Continue?"
        confirmText="Confirm reset"
        tone="warning"
        onConfirm={handleConfirmReset}
      />
    </>
  );
}

/** Converts a timestamp (ms or s) into countdown text like "resets in 3d 2h" or "resets in 15min" */
function formatResetCountdown(nextResetTime?: number): string {
  if (!nextResetTime) return "";
  // Accept both second and millisecond timestamps
  const ts = nextResetTime > 1e12 ? nextResetTime : nextResetTime * 1000;
  const diff = ts - Date.now();
  if (diff <= 0) return "Reset available";
  const min = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (days > 0) return `${days}d ${hours % 24}h until reset`;
  if (hours > 0) return `${hours}h ${min % 60}min until reset`;
  return `${min}min until reset`;
}

function UsageBar({
  label,
  usedPercent,
  windowText,
  resetText,
}: {
  label: string;
  usedPercent: number | null | undefined;
  windowText?: string;
  resetText?: string;
}) {
  const pct = usedPercent ?? 0;
  const barColor = pct >= 80 ? "bg-[var(--danger)]" : pct >= 50 ? "bg-amber-500" : "bg-[var(--brand)]";
  return (
    <div className="space-y-1">
      <div className="flex justify-between items-center">
        <span className="text-[var(--text-tertiary)]">{label}</span>
        <span className="text-[var(--text-primary)] font-medium">{pct.toFixed(1)}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
        <div className={cn("h-full rounded-full transition-all duration-500", barColor)} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      {(windowText || resetText) && (
        <div className="flex justify-between text-[10px] text-[var(--text-tertiary)]">
          <span>{windowText}</span>
          <span>{resetText}</span>
        </div>
      )}
    </div>
  );
}
