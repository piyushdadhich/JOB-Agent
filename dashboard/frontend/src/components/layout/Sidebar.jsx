import { useEffect, useState } from "react";
import {
  LayoutDashboard,
  ListChecks,
  FileText,
  Send,
  History,
  Moon,
  Sun,
  PanelLeftClose,
  PanelLeftOpen,
  Cpu,
  Sparkles,
  KanbanSquare,
  Sliders,
  Settings as SettingsIcon,
} from "lucide-react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/contexts/ThemeContext";
import { cn } from "@/lib/utils";

// FIX-6: health dot in the header. Color reflects /api/health status.
const HEALTH_DOT = {
  healthy: "bg-success",
  degraded: "bg-warning",
  unhealthy: "bg-destructive",
};

const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard", Icon: LayoutDashboard },
  { id: "shortlist", label: "Shortlist", Icon: ListChecks },
  { id: "prompts", label: "Prompts", Icon: FileText },
  { id: "apply", label: "Apply", Icon: Send },
  { id: "pipeline", label: "Pipeline", Icon: KanbanSquare },
  { id: "history", label: "History", Icon: History },
  { id: "expansion", label: "Expansion", Icon: Sparkles },
  { id: "calibrate", label: "Calibration", Icon: Sliders },
  { id: "settings", label: "Settings", Icon: SettingsIcon },
];

export default function Sidebar({ tab, setTab, collapsed, setCollapsed }) {
  const { theme, toggle } = useTheme();
  const ThemeIcon = theme === "dark" ? Sun : Moon;

  // FIX-6: poll /api/health every 60s for the header status dot.
  const [health, setHealth] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .health()
        .then((h) => {
          if (!cancelled) setHealth(h.status);
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <aside
      className={cn(
        "sticky top-0 flex h-screen shrink-0 flex-col self-start border-r border-border bg-card text-card-foreground transition-[width] duration-200",
        collapsed ? "w-[60px]" : "w-[240px]",
      )}
    >
      <div
        className={cn(
          "flex h-14 items-center gap-2 border-b border-border px-3",
          collapsed && "justify-center px-0",
        )}
      >
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-accent text-accent-foreground">
          <Cpu className="h-4 w-4" />
        </div>
        {!collapsed && (
          <div className="flex flex-1 items-center justify-between">
            <div className="flex flex-col leading-tight">
              <span className="text-sm font-semibold">Job Agent</span>
              <span className="text-[10px] text-muted-foreground">
                Operations Dashboard
              </span>
            </div>
            {health && (
              <button
                type="button"
                onClick={() => setTab("dashboard")}
                title={`System health: ${health}`}
                aria-label={`System health: ${health}`}
                className={cn(
                  "mr-1 h-2.5 w-2.5 rounded-full",
                  HEALTH_DOT[health] || "bg-muted-foreground",
                )}
              />
            )}
          </div>
        )}
      </div>

      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-2">
        {NAV_ITEMS.map(({ id, label, Icon }) => {
          const active = tab === id;
          return (
            <button
              key={id}
              onClick={() => setTab(id)}
              title={collapsed ? label : undefined}
              className={cn(
                "flex h-9 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                active
                  ? "bg-accent/15 text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
                collapsed && "justify-center px-0",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!collapsed && <span>{label}</span>}
            </button>
          );
        })}
      </nav>

      <div className="mt-auto flex shrink-0 flex-col gap-1 border-t border-border p-2">
        <Button
          variant="ghost"
          size={collapsed ? "icon" : "sm"}
          onClick={toggle}
          className={cn(
            "justify-start gap-2",
            collapsed && "justify-center",
          )}
          title={theme === "dark" ? "Light mode" : "Dark mode"}
        >
          <ThemeIcon className="h-4 w-4" />
          {!collapsed && (
            <span className="text-xs">
              {theme === "dark" ? "Light mode" : "Dark mode"}
            </span>
          )}
        </Button>
        <Button
          variant="ghost"
          size={collapsed ? "icon" : "sm"}
          onClick={() => setCollapsed(!collapsed)}
          className={cn(
            "justify-start gap-2",
            collapsed && "justify-center",
          )}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? (
            <PanelLeftOpen className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
          {!collapsed && <span className="text-xs">Collapse</span>}
        </Button>
      </div>
    </aside>
  );
}
