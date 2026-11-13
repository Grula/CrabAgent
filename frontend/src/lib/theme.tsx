import { deleteThemeImage, getThemeImage, IMAGE_PREFIX, putThemeImage } from "./theme-images";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Theme = "light" | "dark";
export type ThemeMode = Theme | "system";
/** Font of assistant replies: serif (default) or the UI sans-serif */
export type ChatFont = "serif" | "sans";
export const DEFAULT_COLORS = {
  "light": {
    "bg-primary": "#faf9f5",
    "bg-secondary": "#ffffff",
    "bg-tertiary": "#f0eee6",
    "bg-elevated": "#e8e6dc",
    "text-primary": "#141413",
    "text-secondary": "#3d3d3a",
    "text-tertiary": "#73726c",
    "text-on-accent": "#ffffff",
    "text-on-brand": "#ffffff",
    "border": "#dedcd1",
    "border-subtle": "#ebe9e0",
    "border-strong": "#cbc9bd",
    "brand": "#c96442",
    "brand-hover": "#b65838",
    "brand-active": "#a14d30",
    "accent": "#2c84db",
    "accent-hover": "#1f6fbf",
    "accent-2": "#8a6bbd",
    "accent-2-hover": "#7a5aae",
    "success": "#3d8a4f",
    "warning": "#a86f12",
    "danger": "#b8433a",
    "danger-hover": "#a3372f",
    "agent-researcher": "#3f7cb5",
    "agent-analyst": "#8a6bbd",
    "agent-coder": "#3d8a4f",
    "agent-writer": "#c27a1a",
    "agent-plan_creater": "#1b8a9e",
    "code-bg": "#f3f1ea",
    "code-border": "#e6e4da",
  },
  "dark": {
    "bg-primary": "#262624",
    "bg-secondary": "#30302e",
    "bg-tertiary": "#3a3a37",
    "bg-elevated": "#454541",
    "text-primary": "#f5f4ef",
    "text-secondary": "#c2c0b6",
    "text-tertiary": "#9c9a92",
    "text-on-accent": "#ffffff",
    "text-on-brand": "#ffffff",
    "border": "#42413d",
    "border-subtle": "#34342f",
    "border-strong": "#57564f",
    "brand": "#d97757",
    "brand-hover": "#e2896b",
    "brand-active": "#c4643f",
    "accent": "#5aa1e6",
    "accent-hover": "#79b4ee",
    "accent-2": "#b394dc",
    "accent-2-hover": "#c3aae5",
    "success": "#7cbf86",
    "warning": "#dcae58",
    "danger": "#e5706a",
    "danger-hover": "#ec8984",
    "agent-researcher": "#6fa4d8",
    "agent-analyst": "#b394dc",
    "agent-coder": "#7cbf86",
    "agent-writer": "#e0a64e",
    "agent-plan_creater": "#3fbfd4",
    "code-bg": "#1f1e1d",
    "code-border": "#3a3a37",
  }
} as const;
export type ColorKey = keyof typeof DEFAULT_COLORS.light;
export type ThemeColors = Record<ColorKey, string>;
export interface CustomTheme { id: string; name: string; colors: Record<Theme, ThemeColors>; backgroundImage: string; backgroundOverlay?: number }
const STORAGE_KEY = "crabagent.theme";
const CUSTOM_KEY = "crabagent.customThemes";
const ACTIVE_KEY = "crabagent.activeTheme";
const CHAT_FONT_KEY = "crabagent.chatFont";
const colorKeys = Object.keys(DEFAULT_COLORS.light) as ColorKey[];

/** Pre-upgrade default palette, used to detect and auto-migrate stored themes still on the old colors (e.g. "New theme") */
export const LEGACY_DEFAULT_COLORS = {
  "light": {
    "bg-primary": "#faf7f2",
    "bg-secondary": "#ffffff",
    "bg-tertiary": "#f0ebe2",
    "bg-elevated": "#e6dfd2",
    "text-primary": "#1f1c17",
    "text-secondary": "#6b6358",
    "text-tertiary": "#948a7a",
    "text-on-accent": "#ffffff",
    "text-on-brand": "#ffffff",
    "border": "#e0d8c8",
    "border-subtle": "#ece5d5",
    "border-strong": "#c9bfa9",
    "brand": "#2e8a82",
    "brand-hover": "#3da89e",
    "brand-active": "#1f756d",
    "accent": "#0284c7",
    "accent-hover": "#0ea5e9",
    "accent-2": "#8b6db8",
    "accent-2-hover": "#9d80c8",
    "success": "#4d9659",
    "warning": "#b88a30",
    "danger": "#c8504e",
    "danger-hover": "#d66462",
    "agent-researcher": "#4a82b8",
    "agent-analyst": "#8b6db8",
    "agent-coder": "#4d9659",
    "agent-writer": "#d97706",
    "agent-plan_creater": "#0891b2",
    "code-bg": "#f5f0e5",
    "code-border": "#e0d8c8",
  },
  "dark": {
    "bg-primary": "#1a1814",
    "bg-secondary": "#252320",
    "bg-tertiary": "#33302b",
    "bg-elevated": "#3d3a35",
    "text-primary": "#f5f1e8",
    "text-secondary": "#b8b0a0",
    "text-tertiary": "#8a8275",
    "text-on-accent": "#ffffff",
    "text-on-brand": "#ffffff",
    "border": "#3d3a35",
    "border-subtle": "#2e2c28",
    "border-strong": "#4d4a44",
    "brand": "#3da89e",
    "brand-hover": "#4ec0b5",
    "brand-active": "#2e8a82",
    "accent": "#0ea5e9",
    "accent-hover": "#38bdf8",
    "accent-2": "#b794e0",
    "accent-2-hover": "#c7a8eb",
    "success": "#6ec07a",
    "warning": "#e0b860",
    "danger": "#ef6b6b",
    "danger-hover": "#f58585",
    "agent-researcher": "#6ba0d8",
    "agent-analyst": "#b794e0",
    "agent-coder": "#6ec07a",
    "agent-writer": "#fbbf24",
    "agent-plan_creater": "#22d3ee",
    "code-bg": "#14120f",
    "code-border": "#2e2c28",
  }
} as const;

/** The default palette before the ivory/clay redesign (teal brand). Stored themes that still match it exactly are upgraded. */
export const PREVIOUS_DEFAULT_COLORS = {
  "light": {
    "bg-primary": "#f8f2e7",
    "bg-secondary": "#fffcf5",
    "bg-tertiary": "#f0e6d2",
    "bg-elevated": "#e7dac2",
    "text-primary": "#3e2f21",
    "text-secondary": "#7d6851",
    "text-tertiary": "#a5916f",
    "text-on-accent": "#ffffff",
    "text-on-brand": "#ffffff",
    "border": "#e4d5bc",
    "border-subtle": "#efe3cd",
    "border-strong": "#cbb48e",
    "brand": "#2f8f86",
    "brand-hover": "#3fab9f",
    "brand-active": "#237a72",
    "accent": "#d97e42",
    "accent-hover": "#e59457",
    "accent-2": "#9a6fc0",
    "accent-2-hover": "#ac85cf",
    "success": "#4f9a5c",
    "warning": "#c9922f",
    "danger": "#c8524f",
    "danger-hover": "#d56764",
    "agent-researcher": "#4d86bd",
    "agent-analyst": "#9a6fc0",
    "agent-coder": "#4f9a5c",
    "agent-writer": "#d98a2b",
    "agent-plan_creater": "#0f92b5",
    "code-bg": "#f5edda",
    "code-border": "#e4d5bc",
 
  },
  "dark": {
    "bg-primary": "#221b14",
    "bg-secondary": "#2e251c",
    "bg-tertiary": "#3b3025",
    "bg-elevated": "#483b2e",
    "text-primary": "#f4ebdc",
    "text-secondary": "#c6b599",
    "text-tertiary": "#99886c",
    "text-on-accent": "#ffffff",
    "text-on-brand": "#ffffff",
    "border": "#483b2e",
    "border-subtle": "#332a20",
    "border-strong": "#5e4e3b",
    "brand": "#43b3a6",
    "brand-hover": "#5bc5b8",
    "brand-active": "#2f9488",
    "accent": "#e89a5e",
    "accent-hover": "#f2ad76",
    "accent-2": "#b993e2",
    "accent-2-hover": "#c8a8ec",
    "success": "#70c27c",
    "warning": "#e7b95f",
    "danger": "#f06e6c",
    "danger-hover": "#f68886",
    "agent-researcher": "#6ea4d6",
    "agent-analyst": "#b993e2",
    "agent-coder": "#70c27c",
    "agent-writer": "#f0b45a",
    "agent-plan_creater": "#2cc4de",
    "code-bg": "#191309",
    "code-border": "#332a20",
 
  }
} as const;

/** Upgrades themes whose light or dark palette exactly matches an earlier default; themes the user recolored are untouched */
export function migrateLegacyDefaultThemes(themes: CustomTheme[]): CustomTheme[] {
  const outdated = [LEGACY_DEFAULT_COLORS, PREVIOUS_DEFAULT_COLORS];
  const isLegacyPalette = (mode: Theme, palette: ThemeColors) => outdated.some(old => colorKeys.every(key => palette[key] === old[mode][key]));
  return themes.map(item => (["light", "dark"] as const).some(mode => isLegacyPalette(mode, item.colors[mode]))
    ? { ...item, colors: { light: { ...DEFAULT_COLORS.light }, dark: { ...DEFAULT_COLORS.dark } } }
    : item);
}

export interface ThemeScheme { name: string; backgroundOverlay: number; colors: Record<Theme, ThemeColors> }

/** Exports a theme's colors as portable JSON (no background image — binaries can't be shared across devices) */
export function exportThemeJSON(item: CustomTheme): string {
  return JSON.stringify({ name: item.name, backgroundOverlay: item.backgroundOverlay ?? 0, colors: item.colors }, null, 2);
}

/** Parses imported color JSON: requires name and complete light/dark palettes, overlay optional (0-100) */
export function parseThemeScheme(raw: string): ThemeScheme | null {
  try {
    const item: unknown = JSON.parse(raw);
    if (!item || typeof item !== "object") return null;
    const scheme = item as { name?: unknown; backgroundOverlay?: unknown; colors?: unknown };
    if (typeof scheme.name !== "string" || !scheme.name.trim() || !scheme.colors) return null;
    const palettes = {} as Record<Theme, ThemeColors>;
    for (const mode of ["light", "dark"] as const) {
      const palette = (scheme.colors as Record<string, unknown>)[mode];
      if (!palette || !colorKeys.every(key => isColor((palette as Record<string, unknown>)[key]))) return null;
      palettes[mode] = Object.fromEntries(colorKeys.map(key => [key, (palette as Record<string, unknown>)[key]])) as ThemeColors;
    }
    const backgroundOverlay = typeof scheme.backgroundOverlay === "number" && Number.isFinite(scheme.backgroundOverlay) && scheme.backgroundOverlay >= 0 && scheme.backgroundOverlay <= 100 ? scheme.backgroundOverlay : 0;
    return { name: scheme.name, backgroundOverlay, colors: palettes };
  } catch { return null; }
}
const isColor = (value: unknown): value is string => typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
export function validImage(value: string): boolean {
  if (!value || /^indexeddb:[a-z0-9-]+$/i.test(value)) return true;
  if (/^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=]+$/i.test(value)) return true;
  try { return ["https:", "http:"].includes(new URL(value).protocol); } catch { return false; }
}
export function parseThemes(raw: string | null): CustomTheme[] {
  try {
    const data: unknown = JSON.parse(raw || "[]");
    if (!Array.isArray(data)) return [];
    return data.flatMap((item: any) => {
      if (!item || typeof item.id !== "string" || typeof item.name !== "string" || typeof item.backgroundImage !== "string" || !validImage(item.backgroundImage) || !item.colors) return [];
      const legacy = ["brand", "accent", "background", "text"].every(key => isColor(item.colors[key]));
      const palettes = {} as Record<Theme, ThemeColors>;
      for (const mode of ["light", "dark"] as const) {
        if (legacy) {
          palettes[mode] = { ...DEFAULT_COLORS[mode], brand: item.colors.brand, accent: item.colors.accent, "bg-primary": item.colors.background, "text-primary": item.colors.text };
        } else {
          const palette = item.colors[mode];
          if (!palette || !colorKeys.every(key => isColor(palette[key]))) return [];
          palettes[mode] = Object.fromEntries(colorKeys.map(key => [key, palette[key]])) as ThemeColors;
        }
      }
      const backgroundOverlay = typeof item.backgroundOverlay === "number" && Number.isFinite(item.backgroundOverlay) && item.backgroundOverlay >= 0 && item.backgroundOverlay <= 100 ? item.backgroundOverlay : 40;
      return [{ id: item.id, name: item.name, backgroundImage: item.backgroundImage, backgroundOverlay, colors: palettes }];
    });
  } catch { return []; }
}
interface ThemeContextValue {
  theme: Theme; mode: ThemeMode; setTheme: (t: Theme) => void; setMode: (t: ThemeMode) => void; toggleTheme: () => void;
  colors: ThemeColors | undefined; themes: CustomTheme[]; activeId: string; selectTheme: (id: string) => void; saveTheme: (theme: CustomTheme, image?: Blob) => Promise<void>; deleteTheme: (id: string) => Promise<void>; imageError: string;
  chatFont: ChatFont; setChatFont: (font: ChatFont) => void;
}
const ThemeContext = createContext<ThemeContextValue | null>(null);
const read = (key: string) => { try { return localStorage.getItem(key); } catch { return null; } };
function initialMode(): ThemeMode { const value = read(STORAGE_KEY); return value === "light" || value === "dark" || value === "system" ? value : "system"; }
function initialChatFont(): ChatFont { return read(CHAT_FONT_KEY) === "sans" ? "sans" : "serif"; }
function systemTheme(): Theme { return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark"; }
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(initialMode);
  const [system, setSystem] = useState<Theme>(systemTheme);
  const [themes, setThemes] = useState<CustomTheme[]>(() => {
    const stored = parseThemes(read(CUSTOM_KEY));
    const migrated = migrateLegacyDefaultThemes(stored);
    if (migrated.some((item, i) => item !== stored[i])) {
      try { localStorage.setItem(CUSTOM_KEY, JSON.stringify(migrated)); } catch { /* if migration fails this session still uses the new colors */ }
    }
    return migrated;
  });
  const [activeId, setActiveId] = useState(() => read(ACTIVE_KEY) || "");
  const [imageUrl, setImageUrl] = useState("");
  const [imageError, setImageError] = useState("");
  const [chatFont, setChatFontState] = useState<ChatFont>(initialChatFont);
  useEffect(() => { document.documentElement.setAttribute("data-chat-font", chatFont); }, [chatFont]);
  const setChatFont = (font: ChatFont) => { try { localStorage.setItem(CHAT_FONT_KEY, font); } catch { /* still applies for this session */ } setChatFontState(font); };
  const theme = mode === "system" ? system : mode;
  const active = themes.find(item => item.id === activeId);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: light)");
    if (!media) return;
    const changed = () => setSystem(media.matches ? "light" : "dark");
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    const image = active?.backgroundImage || "";
    setImageUrl("");
    setImageError("");
    if (!image.startsWith(IMAGE_PREFIX)) { setImageUrl(image); return; }
    let cancelled = false;
    let url = "";
    getThemeImage(image.slice(IMAGE_PREFIX.length)).then(blob => {
      if (!blob) throw new Error("Image not found");
      if (!cancelled) { url = URL.createObjectURL(blob); setImageUrl(url); }
    }).catch(() => { if (!cancelled) setImageError("Failed to read the background image. Check browser storage or upload it again."); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [active?.backgroundImage]);
  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", theme);
    const colors = active?.colors[theme];
    for (const key of colorKeys) {
      if (colors) root.style.setProperty(`--${key}`, colors[key]); else root.style.removeProperty(`--${key}`);
    }
    const derived: Record<string, [ColorKey, number]> = {
      "brand-bg": ["brand", 12], "brand-bg-strong": ["brand", 22], "brand-border": ["brand", 35],
      "accent-bg": ["accent", 12], "accent-border": ["accent", 30], "accent-2-bg": ["accent-2", 12],
      "success-bg": ["success", 12], "success-border": ["success", 30], "warning-bg": ["warning", 12], "warning-border": ["warning", 30],
      "danger-bg": ["danger", 12], "danger-border": ["danger", 30],
      ...Object.fromEntries((["researcher", "analyst", "coder", "writer", "plan_creater"] as const).map(name => [`agent-${name}-bg`, [`agent-${name}`, 12]])),
    };
    for (const [key, [base, opacity]] of Object.entries(derived)) {
      if (colors) root.style.setProperty(`--${key}`, `color-mix(in srgb, var(--${base}) ${opacity}%, transparent)`); else root.style.removeProperty(`--${key}`);
    }
    if (imageUrl) {
      root.style.setProperty("--custom-background-image", `url(${JSON.stringify(imageUrl)})`);
      const opacity = active?.backgroundOverlay ?? 0;
      root.style.setProperty("--custom-background-overlay", `linear-gradient(color-mix(in srgb, var(--bg-primary) ${opacity}%, transparent), color-mix(in srgb, var(--bg-primary) ${opacity}%, transparent))`);
    } else {
      root.style.removeProperty("--custom-background-image");
      root.style.removeProperty("--custom-background-overlay");
    }
  }, [theme, active, imageUrl]);
  const setMode = (value: ThemeMode) => { localStorage.setItem(STORAGE_KEY, value); setModeState(value); };
  const selectTheme = (id: string) => { localStorage.setItem(ACTIVE_KEY, id); setActiveId(id); };
  const saveTheme = async (item: CustomTheme, image?: Blob) => {
    if (!item.name.trim() || (item.backgroundOverlay !== undefined && (!Number.isFinite(item.backgroundOverlay) || item.backgroundOverlay < 0 || item.backgroundOverlay > 100)) || !validImage(item.backgroundImage) || !( ["light", "dark"] as const).every(mode => item.colors[mode] && colorKeys.every(key => isColor(item.colors[mode][key])))) throw new Error("Invalid theme");
    const previous = themes.find(t => t.id === item.id)?.backgroundImage || "";
    let newKey = "";
    // Migrate legacy data URLs only when the theme is saved, preserving them on failure.
    if (!image && item.backgroundImage.startsWith("data:")) image = await (await fetch(item.backgroundImage)).blob();
    if (image) {
      newKey = crypto.randomUUID();
      await putThemeImage(newKey, image);
      item = { ...item, backgroundImage: IMAGE_PREFIX + newKey };
    }
    const next = [...themes.filter(t => t.id !== item.id), item];
    try {
      localStorage.setItem(CUSTOM_KEY, JSON.stringify(next));
    } catch (error) {
      if (newKey) await deleteThemeImage(newKey).catch(() => {});
      throw error;
    }
    setThemes(next);
    if (previous.startsWith(IMAGE_PREFIX) && previous !== item.backgroundImage) void deleteThemeImage(previous.slice(IMAGE_PREFIX.length)).catch(() => {});
    selectTheme(item.id);
  };
  const deleteTheme = async (id: string) => {
    const previous = themes.find(t => t.id === id)?.backgroundImage || "";
    const next = themes.filter(t => t.id !== id);
    localStorage.setItem(CUSTOM_KEY, JSON.stringify(next)); setThemes(next);
    if (activeId === id) selectTheme("");
    if (previous.startsWith(IMAGE_PREFIX)) await deleteThemeImage(previous.slice(IMAGE_PREFIX.length));
  };
  return <ThemeContext.Provider value={{ theme, mode, setTheme: setMode, setMode, toggleTheme: () => setMode(theme === "dark" ? "light" : "dark"), colors: active?.colors[theme], themes, activeId, selectTheme, saveTheme, deleteTheme, imageError, chatFont, setChatFont }}>{children}</ThemeContext.Provider>;
}
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
