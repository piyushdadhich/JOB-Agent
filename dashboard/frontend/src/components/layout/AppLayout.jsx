import { useEffect, useState } from "react";
import Sidebar from "./Sidebar";
import GlobalTaskBanner from "@/components/GlobalTaskBanner.jsx";

const COLLAPSED_KEY = "dashboard-sidebar-collapsed";
const NARROW_QUERY = "(max-width: 900px)";

function readCollapsed() {
  if (typeof window === "undefined") return false;
  // Auto-collapse on narrow viewports regardless of stored preference.
  if (window.matchMedia && window.matchMedia(NARROW_QUERY).matches) {
    return true;
  }
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export default function AppLayout({ tab, setTab, header, children }) {
  const [collapsed, setCollapsed] = useState(readCollapsed);

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [collapsed]);

  // Re-collapse when the viewport crosses the narrow threshold.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(NARROW_QUERY);
    const handler = (e) => {
      if (e.matches) setCollapsed(true);
    };
    mql.addEventListener?.("change", handler);
    return () => mql.removeEventListener?.("change", handler);
  }, []);

  return (
    <div className="flex min-h-screen w-full bg-background text-foreground">
      <Sidebar
        tab={tab}
        setTab={setTab}
        collapsed={collapsed}
        setCollapsed={setCollapsed}
      />
      <main className="flex flex-1 flex-col overflow-hidden">
        {header && (
          <header className="flex h-14 items-center justify-between border-b border-border bg-card/40 px-6">
            {header}
          </header>
        )}
        <GlobalTaskBanner />
        <div className="flex-1 overflow-y-auto px-6 py-6">{children}</div>
      </main>
    </div>
  );
}
