import { useTheme, type Theme } from "./theme";

export interface ThemeColors {
  brand: string;
  brandHover: string;
  brandActive: string;
  accent: string;
  accentHover: string;
  accent2: string;
  accent2Hover: string;
  success: string;
  warning: string;
  danger: string;
  dangerHover: string;
  bgPrimary: string;
  bgSecondary: string;
  bgTertiary: string;
  bgElevated: string;
  border: string;
  borderStrong: string;
  textPrimary: string;
  textSecondary: string;
  textTertiary: string;
  agentResearcher: string;
  agentAnalyst: string;
  agentCoder: string;
  agentWriter: string;
}

const DARK: ThemeColors = {
  brand: "#d97757",
  brandHover: "#e2896b",
  brandActive: "#c4643f",
  accent: "#5aa1e6",
  accentHover: "#79b4ee",
  accent2: "#b394dc",
  accent2Hover: "#c3aae5",
  success: "#7cbf86",
  warning: "#dcae58",
  danger: "#e5706a",
  dangerHover: "#ec8984",
  bgPrimary: "#262624",
  bgSecondary: "#30302e",
  bgTertiary: "#3a3a37",
  bgElevated: "#454541",
  border: "#42413d",
  borderStrong: "#57564f",
  textPrimary: "#f5f4ef",
  textSecondary: "#c2c0b6",
  textTertiary: "#9c9a92",
  agentResearcher: "#6fa4d8",
  agentAnalyst: "#b394dc",
  agentCoder: "#7cbf86",
  agentWriter: "#e0a64e",
};

const LIGHT: ThemeColors = {
  brand: "#c96442",
  brandHover: "#b65838",
  brandActive: "#a14d30",
  accent: "#2c84db",
  accentHover: "#1f6fbf",
  accent2: "#8a6bbd",
  accent2Hover: "#7a5aae",
  success: "#3d8a4f",
  warning: "#a86f12",
  danger: "#b8433a",
  dangerHover: "#a3372f",
  bgPrimary: "#faf9f5",
  bgSecondary: "#ffffff",
  bgTertiary: "#f0eee6",
  bgElevated: "#e8e6dc",
  border: "#dedcd1",
  borderStrong: "#cbc9bd",
  textPrimary: "#141413",
  textSecondary: "#3d3d3a",
  textTertiary: "#73726c",
  agentResearcher: "#3f7cb5",
  agentAnalyst: "#8a6bbd",
  agentCoder: "#3d8a4f",
  agentWriter: "#c27a1a",
};

export function getThemeColors(theme: Theme): ThemeColors {
  return theme === "light" ? LIGHT : DARK;
}

export function useThemeColors(): ThemeColors {
  const { theme, colors } = useTheme();
  const defaults = getThemeColors(theme);
  if (!colors) return defaults;
  const result = { ...defaults };
  for (const key of Object.keys(result) as (keyof ThemeColors)[]) {
    const token = key.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`).replace("accent2", "accent-2");
    if (token in colors) result[key] = colors[token as keyof typeof colors];
  }
  return result;
}

