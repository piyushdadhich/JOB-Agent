import { useEffect, useState } from "react";
import { api } from "@/api";
import AppLayout from "@/components/layout/AppLayout";
import Tour from "@/components/Tour.jsx";
import Apply from "@/pages/Apply.jsx";
import Calibration from "@/pages/Calibration.jsx";
import Dashboard from "@/pages/Dashboard.jsx";
import ExpansionInsights from "@/pages/ExpansionInsights.jsx";
import History from "@/pages/History.jsx";
import PipelineTracker from "@/pages/PipelineTracker.jsx";
import Prompts from "@/pages/Prompts.jsx";
import Settings from "@/pages/Settings.jsx";
import Setup from "@/pages/Setup.jsx";
import Shortlist from "@/pages/Shortlist.jsx";

const PAGE_TITLES = {
  dashboard: "Dashboard",
  shortlist: "Shortlist",
  prompts: "Prompts",
  apply: "Apply",
  pipeline: "Pipeline",
  history: "History",
  expansion: "Expansion",
  calibrate: "Calibration",
  settings: "Settings",
};

export default function App() {
  const [tab, setTab] = useState("dashboard");
  // null = still checking; true/false = decision made.
  const [setupComplete, setSetupComplete] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await api.setupStatus();
        if (!cancelled) setSetupComplete(!!status.completed);
      } catch {
        // If the status endpoint is unreachable, assume the user
        // is past setup — better to show the dashboard with an
        // error than block the whole app on a single failed call.
        if (!cancelled) setSetupComplete(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const today = new Date().toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  const header = (
    <>
      <div className="flex flex-col">
        <h1 className="text-base font-semibold leading-none">
          {PAGE_TITLES[tab] ?? "Dashboard"}
        </h1>
        <span className="mt-1 text-xs text-muted-foreground">{today}</span>
      </div>
    </>
  );

  if (setupComplete === null) {
    return null;
  }
  if (setupComplete === false) {
    return <Setup onComplete={() => setSetupComplete(true)} />;
  }

  return (
    <>
      <AppLayout tab={tab} setTab={setTab} header={header}>
        {tab === "dashboard" && <Dashboard goToTab={setTab} />}
        {tab === "shortlist" && <Shortlist goToTab={setTab} />}
        {tab === "prompts" && <Prompts />}
        {tab === "apply" && <Apply />}
        {tab === "pipeline" && <PipelineTracker />}
        {tab === "history" && <History />}
        {tab === "expansion" && <ExpansionInsights />}
        {tab === "calibrate" && <Calibration />}
        {tab === "settings" && <Settings />}
      </AppLayout>
      <Tour setTab={setTab} />
    </>
  );
}
