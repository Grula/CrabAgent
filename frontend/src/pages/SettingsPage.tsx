import { useState, useEffect } from "react";
import { Save, FlaskConical, Search, Check, SlidersHorizontal, Globe, Wifi, ShieldCheck, Bell } from "lucide-react";
import { Input, Button } from "../components/ui";
import { toast } from "../components/ui/Toast";
import { cn } from "../lib/cn";
import * as settingsApi from "../api/settings";
import ModelSelector from "../components/ModelSelector";
import { SubAgentModelMapEditor, parseModelMap, serializeModelMap, type ModelMapRow } from "../components/SubAgentModelMapEditor";
import PushPanel from "../components/PushPanel";
import ThemeSettingsPanel from "../components/ThemeSettingsPanel";
import { useSettingsData } from "../hooks/useSettingsData";

type SettingsTab = "appearance" | "general" | "search" | "privacy" | "network" | "push";

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("general");

  // Cached store — survives tab switches (no reload flash on re-mount).
  // settings (fast) and providerModels (slow) load independently so the
  // SearXNG / proxy fields render immediately; only the ModelSelector
  // dropdowns wait for providers.
  const { settings, providerModels, settingsLoading, providersLoading, updateSettings } = useSettingsData();

  // Local edit state — seeded from cache, updated as the user types.
  const [defaultModel, setDefaultModel] = useState("");
  const [defaultModelProvider, setDefaultModelProvider] = useState<string | undefined>(undefined);
  const [searxngUrl, setSearxngUrl] = useState("");
  const [globalProxy, setGlobalProxy] = useState("");
  const [webProxy, setWebProxy] = useState("");
  const [llmProxy, setLlmProxy] = useState("");
  const [browserProxy, setBrowserProxy] = useState("");
  const [subAgentMapRows, setSubAgentMapRows] = useState<ModelMapRow[]>([]);
  const [conversationHistoryEnabled, setConversationHistoryEnabled] = useState(false);
  const [seeded, setSeeded] = useState(false);

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testingProxy, setTestingProxy] = useState(false);

  // Seed local form state as soon as the fast settings track arrives.
  useEffect(() => {
    if (!settings) return;
    setDefaultModel(settings.default_model);
    setDefaultModelProvider(settings.default_model_provider || undefined);
    setSearxngUrl(settings.searxng_url);
    setGlobalProxy(settings.proxy);
    setWebProxy(settings.web_proxy);
    setLlmProxy(settings.llm_proxy);
    setBrowserProxy(settings.browser_proxy);
    setSubAgentMapRows(parseModelMap(settings.sub_agent_model_map));
    setConversationHistoryEnabled(settings.conversation_history_tool_enabled === "true");
    setSeeded(true);
  }, [settings]);

  // Once providers arrive, resolve the default-model provider if the
  // settings payload didn't carry one.
  useEffect(() => {
    if (providerModels.length === 0 || !defaultModel || defaultModelProvider) return;
    const resolved = resolveProviderForModel(providerModels, defaultModel);
    if (resolved) setDefaultModelProvider(resolved);
  }, [providerModels, defaultModel, defaultModelProvider]);

  const handleModelChange = (modelId: string, providerName: string) => {
    setDefaultModel(modelId);
    setDefaultModelProvider(providerName);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const data: Record<string, string> = {};
      if (defaultModel) {
        data.default_model = defaultModel;
        if (defaultModelProvider) {
          data.default_model_provider = defaultModelProvider;
        }
      } else {
        data.default_model = "";
        data.default_model_provider = "";
      }
      data.searxng_url = searxngUrl;
      data.proxy = globalProxy;
      data.web_proxy = webProxy;
      data.llm_proxy = llmProxy;
      data.browser_proxy = browserProxy;
      data.sub_agent_model_map = serializeModelMap(subAgentMapRows);
      data.conversation_history_tool_enabled = String(conversationHistoryEnabled);
      await settingsApi.updateSettings(data);
      // Sync cache so the next visit shows saved values without refetch.
      updateSettings({
        default_model: defaultModel,
        default_model_provider: defaultModelProvider,
        searxng_url: searxngUrl,
        proxy: globalProxy,
        web_proxy: webProxy,
        llm_proxy: llmProxy,
        browser_proxy: browserProxy,
        sub_agent_model_map: data.sub_agent_model_map,
        conversation_history_tool_enabled: data.conversation_history_tool_enabled,
      });
      toast.success("Settings saved");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleTestSearxng = async () => {
    if (!searxngUrl) return;
    setTesting(true);
    try {
      const result = await settingsApi.testSearxng(searxngUrl);
      if (result.success) {
        toast.success(
          `Connection successful (${result.result_count ?? 0} results)`,
        );
      } else {
        toast.error(
          `Connection failed: ${result.error ?? "unknown"}`,
        );
      }
    } catch (e: unknown) {
      toast.error(
        `Connection failed: ${e instanceof Error ? e.message : "unknown"}`,
      );
    } finally {
      setTesting(false);
    }
  };

  const handleTestProxy = async () => {
    if (!globalProxy) return;
    setTestingProxy(true);
    try {
      const result = await settingsApi.testProxy(globalProxy);
      if (result.success) {
        toast.success(
          `Proxy works (${result.latency_ms ?? 0}ms, IP: ${result.ip ?? ""})`,
        );
      } else {
        toast.error(
          `Connection failed: ${result.error ?? "unknown"}`,
        );
      }
    } catch (e: unknown) {
      toast.error(
        `Connection failed: ${e instanceof Error ? e.message : "unknown"}`,
      );
    } finally {
      setTestingProxy(false);
    }
  };

  // Only block on the fast settings track. Providers (model lists) load in
  // the background; the ModelSelector shows an inline placeholder until then.
  if (settingsLoading || !seeded || !settings) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-sm text-[var(--text-tertiary)]">
          {"Loading…"}
        </div>
      </div>
    );
  }

  const tabs: { id: SettingsTab; label: string; icon: React.ReactNode }[] = [
    { id: "appearance", label: "Appearance", icon: <SlidersHorizontal size={14} /> },
    { id: "general", label: "General", icon: <SlidersHorizontal size={14} /> },
    { id: "search", label: "Search Engine", icon: <Search size={14} /> },
    { id: "privacy", label: "Privacy", icon: <ShieldCheck size={14} /> },
    { id: "network", label: "Network", icon: <Globe size={14} /> },
    { id: "push", label: "Mobile push", icon: <Bell size={14} /> },
  ];

  const showSaveButton = activeTab === "general" || activeTab === "search" || activeTab === "privacy" || activeTab === "network";

  return (
    <div className="h-full min-w-0 flex flex-col p-4 sm:p-8 max-w-5xl mx-auto w-full">
      <h1 className="text-xl font-semibold text-[var(--text-primary)] mb-4">
        {"Settings"}
      </h1>

      <select aria-label={"Settings"} value={activeTab} onChange={(event) => setActiveTab(event.target.value as SettingsTab)} className="sm:hidden mb-4 w-full rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]">
        {tabs.map((tab) => <option key={tab.id} value={tab.id}>{tab.label}</option>)}
      </select>
      {/* Desktop tabs remain available at wider widths. */}
      <div className="hidden sm:flex gap-0.5 mb-6 border-b border-[var(--border)] overflow-x-auto">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "flex items-center gap-1.5 px-4 py-2 text-sm font-medium transition-all whitespace-nowrap shrink-0",
              "border-b-2 -mb-px rounded-t-lg",
              activeTab === tab.id
                ? "border-[var(--brand)] text-[var(--brand)]"
                : "border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]/50",
            )}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="flex-1 min-h-0 min-w-0 overflow-y-auto pb-4">
        {activeTab === "appearance" && <ThemeSettingsPanel />}
        {activeTab === "general" && (
          <div className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl p-4 sm:p-5 space-y-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--text-secondary)]">
                {"Default LLM Model"}
              </label>
              <div className="flex items-center gap-2">
                {providersLoading ? (
                  <div className="h-9 flex items-center text-xs text-[var(--text-tertiary)]">
                    {"Loading…"}
                  </div>
                ) : (
                  <ModelSelector
                    providerModels={providerModels}
                    selectedModel={defaultModel || ""}
                    selectedProvider={defaultModelProvider}
                    onChange={handleModelChange}
                    dropdownUpward={false}
                  />
                )}
              </div>
              <p className="text-xs text-[var(--text-tertiary)]">
                {"Used by email processing, scheduled tasks, and other automated workflows when no model is specified."}
              </p>
            </div>

            {/* Sub-Agent Model Map */}
            <div className="flex flex-col gap-1.5 pt-2 border-t border-[var(--border)]">
              <label className="text-xs font-medium text-[var(--text-secondary)]">
                {"Sub-Agent Model Mapping"}
              </label>
              <p className="text-xs text-[var(--text-tertiary)] mb-1">
                {"When the main agent delegates using the left model, the sub-agent switches to the right model (cross-provider allowed, for cost control). Unmatched combos fall through to the default chain."}
              </p>
              {providersLoading ? (
                <div className="h-9 flex items-center text-xs text-[var(--text-tertiary)]">
                  {"Loading…"}
                </div>
              ) : (
                <SubAgentModelMapEditor
                  rows={subAgentMapRows}
                  onChange={setSubAgentMapRows}
                  providerModels={providerModels}
                />
              )}
            </div>
          </div>
        )}

        {activeTab === "search" && (
          <div className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl p-4 sm:p-5 space-y-4">
            <Input
              label={"SearXNG URL"}
              hint={"Self-hosted search engine. Leave empty to use fallback web search."}
              placeholder={"http://localhost:8888"}
              value={searxngUrl}
              onChange={(e) => setSearxngUrl(e.target.value)}
              leftIcon={<Search size={14} />}
            />
            <div className="flex gap-2 pt-1">
              <Button
                variant="secondary"
                size="sm"
                onClick={handleTestSearxng}
                disabled={!searxngUrl || testing}
              >
                {testing ? (
                  <>
                    <FlaskConical size={14} className="animate-spin" />
                    {"Testing…"}
                  </>
                ) : (
                  <>
                    <FlaskConical size={14} />
                    {"Test Connection"}
                  </>
                )}
              </Button>
            </div>
          </div>
        )}

        {activeTab === "privacy" && (
          <div className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl p-4 sm:p-5 space-y-4">
            <div className="flex items-start justify-between gap-5">
              <div className="space-y-1">
                <h2 className="text-sm font-medium text-[var(--text-primary)]">Allow the agent to search past sessions</h2>
                <p className="text-xs leading-5 text-[var(--text-tertiary)] max-w-lg">
                  When enabled, the agent can search and read your other sessions and workspaces. Results are sent as context to the currently selected model service.
                </p>
              </div>
              <input
                aria-label="Allow agent to search past sessions"
                type="checkbox"
                checked={conversationHistoryEnabled}
                onChange={(event) => setConversationHistoryEnabled(event.target.checked)}
                className="mt-1 h-4 w-4 shrink-0 accent-[var(--brand)]"
              />
            </div>
            <p className="rounded-lg bg-[var(--warning-bg)] px-3 py-2 text-xs leading-5 text-[var(--text-secondary)]">
              Historical content may contain untrusted instructions. Only enable this if you trust the current model service; other users' sessions are never accessible.
            </p>
          </div>
        )}

        {activeTab === "network" && (
          <div className="bg-[var(--bg-secondary)] border border-[var(--border)] rounded-xl p-4 sm:p-5 space-y-4">
            <div className="flex items-center gap-2 mb-2">
              <Wifi size={14} className="text-[var(--brand)]" />
              <span className="text-xs font-medium text-[var(--text-secondary)]">
                {"Proxy Settings"}
              </span>
            </div>

            {/* Global Proxy */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--text-secondary)]">
                {"Global Proxy"}
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={globalProxy}
                  onChange={(e) => setGlobalProxy(e.target.value)}
                  placeholder="http://127.0.0.1:7890"
                  className="flex-1 h-9 px-3 text-sm rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)]/30"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleTestProxy}
                  disabled={!globalProxy || testingProxy}
                >
                  {testingProxy ? (
                    <>
                      <FlaskConical size={14} className="animate-spin" />
                      {"Testing…"}
                    </>
                  ) : (
                    <>
                      <FlaskConical size={14} />
                      {"Test"}
                    </>
                  )}
                </Button>
              </div>
              <p className="text-xs text-[var(--text-tertiary)]">
                {"Default proxy for all network requests. Supports http:// and socks5:// protocols."}
              </p>
            </div>

            {/* Divider */}
            <div className="border-t border-[var(--border)] pt-4">
              <p className="text-[11px] font-medium text-[var(--text-tertiary)] mb-3">
                {"Category-specific proxies (leave empty to use global proxy)"}
              </p>

              {/* Web Proxy */}
              <div className="flex flex-col gap-1.5 mb-3">
                <label className="text-xs font-medium text-[var(--text-secondary)]">
                  {"Web Tools Proxy"}
                </label>
                <input
                  type="text"
                  value={webProxy}
                  onChange={(e) => setWebProxy(e.target.value)}
                  placeholder={"Leave empty to use global proxy"}
                  className="w-full h-9 px-3 text-sm rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)]/30"
                />
              </div>

              {/* LLM Proxy */}
              <div className="flex flex-col gap-1.5 mb-3">
                <label className="text-xs font-medium text-[var(--text-secondary)]">
                  {"LLM Calls Proxy"}
                </label>
                <input
                  type="text"
                  value={llmProxy}
                  onChange={(e) => setLlmProxy(e.target.value)}
                  placeholder={"Leave empty to use global proxy"}
                  className="w-full h-9 px-3 text-sm rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)]/30"
                />
              </div>

              {/* Browser Proxy */}
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-[var(--text-secondary)]">
                  {"Browser Proxy"}
                </label>
                <input
                  type="text"
                  value={browserProxy}
                  onChange={(e) => setBrowserProxy(e.target.value)}
                  placeholder={"Leave empty to use global proxy"}
                  className="w-full h-9 px-3 text-sm rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)]/30"
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === "push" && <PushPanel />}

      </div>

      {/* Save Button — only for general/search tabs */}
      {showSaveButton && (
        <div className="sticky bottom-0 py-3 bg-[var(--bg-primary)] border-t border-[var(--border)]">
          <Button
            variant="brand"
            onClick={handleSave}
            disabled={saving}
            className="w-full sm:w-auto"
          >
            {saving ? (
              <>
                <Save size={15} className="animate-spin" />
                {"Saving…"}
              </>
            ) : (
              <>
                <Check size={15} />
                {"Save Settings"}
              </>
            )}
          </Button>
        </div>
      )}
    </div>
  );
}

/** Find the provider name that owns a given model id, if any. */
function resolveProviderForModel(
  providerModels: { provider: { name: string }; models: { id: string }[] }[],
  modelId: string,
): string | undefined {
  if (!modelId) return undefined;
  for (const pm of providerModels) {
    if (pm.models.some((m) => m.id === modelId)) {
      return pm.provider.name;
    }
  }
  return undefined;
}
