import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

// Theme model:
//   - `preference` is the user's stored choice: "light" | "dark" | "system"
//   - `theme` is the effective theme actually applied. When preference is
//     "system" it follows the OS prefers-color-scheme media query.
// Backward compatibility:
//   - The Sidebar still calls `toggle()` to flip between light and dark.
//     `toggle()` cycles light <-> dark (it never picks "system"); explicit
//     "system" selection is done from the Settings page.
const ThemeContext = createContext({
  theme: "light",
  preference: "light",
  setPreference: () => {},
  toggle: () => {},
});

const STORAGE_KEY = "dashboard-theme";
const VALID = ["light", "dark", "system"];

function readPreference() {
  if (typeof window === "undefined") return "light";
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (VALID.includes(stored)) return stored;
  } catch {
    /* ignore */
  }
  return "light";
}

function systemPrefersDark() {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function ThemeProvider({ children }) {
  const [preference, setPreferenceState] = useState(readPreference);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  // Track OS theme changes so "system" preference stays live.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e) => setSystemDark(e.matches);
    mql.addEventListener?.("change", handler);
    return () => mql.removeEventListener?.("change", handler);
  }, []);

  // Effective theme.
  const theme =
    preference === "system"
      ? (systemDark ? "dark" : "light")
      : preference;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const setPreference = useCallback((pref) => {
    if (!VALID.includes(pref)) return;
    setPreferenceState(pref);
    try {
      window.localStorage.setItem(STORAGE_KEY, pref);
    } catch {
      /* ignore */
    }
  }, []);

  // Backward-compatible toggle for the Sidebar moon/sun button. Cycles
  // light <-> dark; if the user is on "system" we flip to whichever
  // explicit choice differs from the current effective theme.
  const toggle = useCallback(() => {
    setPreference(theme === "dark" ? "light" : "dark");
  }, [theme, setPreference]);

  return (
    <ThemeContext.Provider
      value={{ theme, preference, setPreference, toggle }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
