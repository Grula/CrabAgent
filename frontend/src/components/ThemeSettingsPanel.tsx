import { useState } from "react";
import { DEFAULT_COLORS, exportThemeJSON, parseThemeScheme, useTheme, validImage, type ChatFont, type CustomTheme, type ColorKey, type Theme, type ThemeMode } from "../lib/theme";

const inputClass = "w-full rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]";
const buttonClass = "rounded-lg border border-[var(--border)] px-3 py-2 text-sm hover:border-[var(--brand)]";
const IMPORT_PLACEHOLDER = '{ "name": "New theme", "backgroundOverlay": 40, "colors": { "light": { ...31 color keys }, "dark": { ... } } }';
export default function ThemeSettingsPanel() {
  const { theme, mode, setMode, themes, activeId, selectTheme, saveTheme, deleteTheme, imageError, chatFont, setChatFont } = useTheme();
  const [draft, setDraft] = useState<CustomTheme | null>(null);
  const [editingMode, setEditingMode] = useState<Theme>(theme);
  const [imageFile, setImageFile] = useState<File | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const create = () => { setImageFile(undefined); setError(""); setDraft({ id: crypto.randomUUID(), name: "New theme", colors: { light: { ...DEFAULT_COLORS.light }, dark: { ...DEFAULT_COLORS.dark } }, backgroundImage: "", backgroundOverlay: 50 }); };
  const groups: [string, [ColorKey, string][]][] = [
    ["Background & panels", [["bg-primary", "Main background"], ["bg-secondary", "Panel"], ["bg-tertiary", "Secondary background"], ["bg-elevated", "Overlay"]]],
    ["Text & borders", [["text-primary", "Primary text"], ["text-secondary", "Secondary text"], ["text-tertiary", "Tertiary text"], ["text-on-brand", "Text on brand"], ["text-on-accent", "Text on accent"], ["border", "Border"], ["border-subtle", "Subtle border"], ["border-strong", "Strong border"]]],
    ["Brand & accent", [["brand", "Brand"], ["brand-hover", "Brand hover"], ["brand-active", "Brand active"], ["accent", "Accent"], ["accent-hover", "Accent hover"], ["accent-2", "Accent alt"], ["accent-2-hover", "Accent hover alt"]]],
    ["Status & code", [["success", "Success"], ["warning", "Warning"], ["danger", "Danger"], ["danger-hover", "Danger hover"], ["code-bg", "Code background"], ["code-border", "Code border"]]],
    ["Agents & charts", [["agent-researcher", "Research"], ["agent-analyst", "Analysis"], ["agent-coder", "Code"], ["agent-writer", "Writing"], ["agent-plan_creater", "Planning"]]],
  ];
  const safely = (action: () => void) => { try { action(); setError(""); } catch { setError("Save failed. Check the data or browser storage space."); } };
  return <div className="space-y-4">
    <section className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-5 space-y-3">
      <h2 className="font-semibold">Appearance mode</h2>
      <p className="text-xs text-[var(--text-secondary)]">When following the system, the appearance switches automatically with system settings.</p>
      <div className="flex flex-wrap gap-2">{([ ["light", "Light"], ["dark", "Dark"], ["system", "Follow system"] ] as [ThemeMode, string][]).map(([value, label]) => <button key={value} className={buttonClass} aria-pressed={mode === value} style={mode === value ? { borderColor: "var(--brand)", color: "var(--brand)" } : undefined} onClick={() => safely(() => setMode(value))}>{label}</button>)}</div>
    </section>
    <section className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-5 space-y-3">
      <h2 className="font-semibold">Chat font</h2>
      <p className="text-xs text-[var(--text-secondary)]">The typeface of assistant replies. Code, tables and the rest of the interface keep their own fonts.</p>
      <div className="flex flex-wrap gap-2">{([ ["serif", "Serif", "var(--font-serif)"], ["sans", "Sans-serif", "var(--font-sans)"] ] as [ChatFont, string, string][]).map(([value, label, family]) => <button key={value} className={buttonClass} aria-pressed={chatFont === value} style={{ fontFamily: family, ...(chatFont === value ? { borderColor: "var(--brand)", color: "var(--brand)" } : {}) }} onClick={() => safely(() => setChatFont(value))}>{label}</button>)}</div>
    </section>
    <section className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="font-semibold">Theme management</h2><p className="text-xs text-[var(--text-secondary)]">Themes are stored in this browser; colors and images override the current appearance.</p></div><button className={buttonClass} onClick={create}>Create theme</button></div>
      <div className="flex flex-wrap gap-2"><button className={buttonClass} aria-pressed={!activeId} onClick={() => safely(() => selectTheme(""))}>Default theme{!activeId ? " ✓" : ""}</button>{themes.map(item => <div key={item.id} className="flex items-center gap-1 rounded-lg border border-[var(--border)] p-1"><button className="px-2 py-1 text-sm" aria-pressed={activeId === item.id} onClick={() => safely(() => selectTheme(item.id))}>{item.name}{activeId === item.id ? " ✓" : ""}</button><button className="px-2 py-1 text-xs" onClick={() => { setImageFile(undefined); setDraft({ ...item, colors: { light: { ...item.colors.light }, dark: { ...item.colors.dark } } }); setError(""); }}>Edit</button><button className="px-2 py-1 text-xs text-[var(--danger)]" disabled={busy} onClick={async () => { setBusy(true); try { await deleteTheme(item.id); if (draft?.id === item.id) setDraft(null); setError(""); } catch { setError("Delete failed. Check browser storage."); } finally { setBusy(false); } }}>Delete</button><button className="px-2 py-1 text-xs" onClick={() => { navigator.clipboard?.writeText(exportThemeJSON(item)).then(() => { setError(""); setNotice(`Copied the ${item.name} palette JSON to the clipboard`); }).catch(() => setError("Copy failed. Export it manually.")); }}>Export</button></div>)}</div>
      {draft && <div className="space-y-4 border-t border-[var(--border)] pt-4"><label className="block text-sm">Theme name<input className={`${inputClass} mt-1`} maxLength={40} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label><div className="flex gap-2">{([ ["light", "Edit light palette"], ["dark", "Edit dark palette"] ] as [Theme, string][]).map(([value, label]) => <button key={value} className={buttonClass} aria-pressed={editingMode === value} style={editingMode === value ? { borderColor: "var(--brand)", color: "var(--brand)" } : undefined} onClick={() => setEditingMode(value)}>{label}</button>)}</div>{groups.map(([group, fields]) => <fieldset key={group}><legend className="mb-2 text-sm font-semibold">{group}</legend><div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{fields.map(([key, label]) => <label key={key} className="text-sm">{label}<input className="mt-1 block h-10 w-full cursor-pointer rounded border border-[var(--border)] bg-transparent" type="color" value={draft.colors[editingMode][key]} onChange={e => setDraft({ ...draft, colors: { ...draft.colors, [editingMode]: { ...draft.colors[editingMode], [key]: e.target.value } } })} /></label>)}</div></fieldset>)}
        <label className="block text-sm">Background image URL<input className={`${inputClass} mt-1`} placeholder="https://..." value={draft.backgroundImage.startsWith("data:") || draft.backgroundImage.startsWith("indexeddb:") ? "" : draft.backgroundImage} onChange={e => { setImageFile(undefined); setDraft({ ...draft, backgroundImage: e.target.value }); }} /></label><label className="block text-sm">Or upload an image (max 10 MB)<input className="mt-1 block w-full text-xs" type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 10 * 1024 * 1024 || !["image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.type)) { setError("Choose a PNG, JPEG, GIF or WebP image of at most 10 MB."); e.target.value = ""; return; } setImageFile(file); setError(""); }} /></label>{(draft.backgroundImage || imageFile) && <button className={buttonClass} onClick={() => { setImageFile(undefined); setDraft({ ...draft, backgroundImage: "" }); }}>Remove background image</button>}
        <div className="space-y-2">
          <label htmlFor="theme-background-overlay" className="flex items-center justify-between text-sm"><span>Background overlay opacity</span><span>{draft.backgroundOverlay ?? 0}%</span></label>
          <input id="theme-background-overlay" className="w-full accent-[var(--brand)]" type="range" min="0" max="100" step="5" value={draft.backgroundOverlay ?? 0} onChange={e => setDraft({ ...draft, backgroundOverlay: Number(e.target.value) })} />
          <p className="text-xs text-[var(--text-secondary)]">The overlay uses the main background color of the current mode. Higher values fade the image and improve text contrast; it applies after you save.</p>
        </div>
        <div className="flex gap-2"><button className={buttonClass} disabled={busy} onClick={async () => { if (!draft.name.trim() || !validImage(draft.backgroundImage)) { setError("Enter a name and a valid HTTP(S) image URL."); return; } setBusy(true); try { await saveTheme({ ...draft, name: draft.name.trim() }, imageFile); setDraft(null); setImageFile(undefined); setError(""); } catch { setError("Save failed. Check browser storage space or permissions."); } finally { setBusy(false); } }}>Save and apply</button><button className={buttonClass} disabled={busy} onClick={() => { setDraft(null); setImageFile(undefined); setError(""); }}>Cancel</button></div></div>}
      <div className="space-y-2 border-t border-[var(--border)] pt-3">
        <div className="flex items-center gap-2">
          <button className={buttonClass} onClick={() => setImportOpen(value => !value)}>Import palette JSON</button>
          <span className="text-xs text-[var(--text-secondary)]">Paste exported JSON to import: if an existing theme has the same name its palette is overwritten (the background image is kept), otherwise a new theme is created.</span>
        </div>
        {importOpen && <>
          <textarea className={`${inputClass} font-mono text-xs`} rows={8} value={importText} placeholder={IMPORT_PLACEHOLDER} onChange={e => setImportText(e.target.value)} />
          <button className={buttonClass} disabled={busy} onClick={async () => {
            const scheme = parseThemeScheme(importText);
            if (!scheme) { setError("Invalid palette JSON: it needs a name and a complete light and dark palette."); setNotice(""); return; }
            const existing = themes.find(item => item.name === scheme.name);
            setBusy(true);
            try {
              await saveTheme(existing ? { ...existing, colors: scheme.colors, backgroundOverlay: scheme.backgroundOverlay } : { id: crypto.randomUUID(), name: scheme.name, colors: scheme.colors, backgroundImage: "", backgroundOverlay: scheme.backgroundOverlay });
              setImportText(""); setImportOpen(false); setError(""); setNotice("Palette imported and applied.");
            } catch { setError("Import failed. Check browser storage space or permissions."); } finally { setBusy(false); }
          }}>Import and apply</button>
        </>}
      </div>
      {imageError && <p role="alert" className="text-sm text-[var(--danger)]">{imageError}</p>}
      {error && <p role="alert" className="text-sm text-[var(--danger)]">{error}</p>}
      {notice && <p className="text-sm text-[var(--success)]">{notice}</p>}
    </section>
  </div>;
}
