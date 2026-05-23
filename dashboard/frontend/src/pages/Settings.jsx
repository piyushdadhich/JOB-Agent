import { useEffect, useState } from "react";
import { Palette, ScrollText } from "lucide-react";
import { api } from "@/api";
import { useTheme } from "@/contexts/ThemeContext";
import ActivityLogViewer from "@/components/ActivityLogViewer.jsx";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

// FIX-5: editable applicant fields → contact block in resume / CL
// prompts. The Spec-10 ApplicantSettings model only enumerates
// first/last/email/phone/linkedin; the open-ended PUT /applicant
// endpoint accepts these extra fields too (city, province,
// portfolio_url) and merges them in without disturbing other keys.
const APPLICANT_FIELDS = [
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "city", label: "City" },
  { key: "province", label: "Province" },
  { key: "linkedin_url", label: "LinkedIn URL" },
  { key: "portfolio_url", label: "Portfolio / GitHub URL" },
];

function csvSplit(s) {
  return s
    .split(/\r?\n|,/)
    .map((t) => t.trim())
    .filter(Boolean);
}

function Section({ title, children, onSave, savedAt, error, busy }) {
  return (
    <Card>
      <CardContent className="space-y-3 p-4 text-sm">
        <div className="flex items-baseline justify-between">
          <div className="font-semibold">{title}</div>
          {savedAt && (
            <span className="text-xs text-success">Saved {savedAt}</span>
          )}
        </div>
        {children}
        {error && (
          <div className="rounded border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}
        {onSave && (
          <div className="flex justify-end">
            <Button size="sm" onClick={onSave} disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function nowHHMM() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes(),
  ).padStart(2, "0")}`;
}

function useSection(loader, defaults = null) {
  const [data, setData] = useState(defaults);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState(null);

  useEffect(() => {
    let cancelled = false;
    loader()
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message || String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return {
    data, setData, error, setError, busy, setBusy, savedAt, setSavedAt,
    saveOk: () => setSavedAt(nowHHMM()),
  };
}

function ProfileSection() {
  const s = useSection(() => api.settingsGetProfile());
  if (s.data === null) return <Skeleton className="h-40 w-full" />;
  const d = s.data;

  const update = (patch) => s.setData({ ...d, ...patch });
  const save = async () => {
    s.setBusy(true);
    s.setError(null);
    try {
      const r = await api.settingsSaveProfile(d);
      s.setData(r);
      s.saveOk();
    } catch (e) {
      s.setError(e.message || String(e));
    } finally {
      s.setBusy(false);
    }
  };

  return (
    <Section
      title="Profile"
      onSave={save}
      busy={s.busy}
      error={s.error}
      savedAt={s.savedAt}
    >
      <div className="space-y-2">
        <label className="text-xs text-muted-foreground">Display name</label>
        <Input
          value={d.display_name}
          onChange={(e) => update({ display_name: e.target.value })}
        />
        <label className="text-xs text-muted-foreground">
          Target cities (one per line)
        </label>
        <textarea
          value={(d.target_cities || []).join("\n")}
          onChange={(e) =>
            update({ target_cities: csvSplit(e.target.value) })
          }
          rows={3}
          className="w-full rounded border border-border bg-background p-2 text-xs"
        />
        <label className="text-xs text-muted-foreground">
          Target role types (one per line)
        </label>
        <textarea
          value={(d.target_role_types || []).join("\n")}
          onChange={(e) =>
            update({ target_role_types: csvSplit(e.target.value) })
          }
          rows={3}
          className="w-full rounded border border-border bg-background p-2 text-xs"
        />
        <div className="grid grid-cols-3 gap-2">
          <div>
            <label className="text-xs text-muted-foreground">
              Salary floor
            </label>
            <Input
              type="number"
              value={d.salary?.floor ?? 0}
              onChange={(e) =>
                update({
                  salary: { ...d.salary, floor: Number(e.target.value) || 0 },
                })
              }
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Target</label>
            <Input
              type="number"
              value={d.salary?.target ?? 0}
              onChange={(e) =>
                update({
                  salary: { ...d.salary, target: Number(e.target.value) || 0 },
                })
              }
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Cap</label>
            <Input
              type="number"
              value={d.salary?.cap ?? 0}
              onChange={(e) =>
                update({
                  salary: { ...d.salary, cap: Number(e.target.value) || 0 },
                })
              }
            />
          </div>
        </div>
        <label className="text-xs text-muted-foreground">
          Employer size
        </label>
        <Input
          value={d.employer_size_priority || ""}
          onChange={(e) =>
            update({ employer_size_priority: e.target.value || null })
          }
          placeholder="small | mid_sized | large | enterprise"
        />
        <label className="text-xs text-muted-foreground">
          Remote preference
        </label>
        <Input
          value={d.remote_preference || ""}
          onChange={(e) =>
            update({ remote_preference: e.target.value || null })
          }
          placeholder="remote | hybrid | onsite | any"
        />
      </div>
    </Section>
  );
}

function ApplicantSection() {
  // FIX-5: render APPLICANT_FIELDS dynamically instead of hard-coding
  // five inputs. Save uses the open-ended PUT endpoint so untouched
  // keys in the YAML (education, certifications, work_history, …)
  // are preserved by the backend's merge-on-write semantics.
  const s = useSection(() => api.settingsGetApplicant());
  if (s.data === null) return <Skeleton className="h-40 w-full" />;
  const d = s.data || {};
  const update = (patch) => s.setData({ ...d, ...patch });
  const save = async () => {
    s.setBusy(true);
    s.setError(null);
    try {
      const payload = {};
      for (const f of APPLICANT_FIELDS) {
        payload[f.key] = d[f.key] ?? "";
      }
      const updated = await api.settingsUpdateApplicant(payload);
      s.setData(updated);
      s.saveOk();
    } catch (e) {
      s.setError(e.message || String(e));
    } finally {
      s.setBusy(false);
    }
  };
  return (
    <Section
      title="Applicant identity"
      onSave={save}
      busy={s.busy}
      error={s.error}
      savedAt={s.savedAt}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {APPLICANT_FIELDS.map((f) => (
          <div key={f.key} className="space-y-1">
            <label className="text-xs text-muted-foreground">
              {f.label}
            </label>
            <Input
              value={d[f.key] ?? ""}
              onChange={(e) => update({ [f.key]: e.target.value })}
              placeholder={f.label}
              type={f.key === "email" ? "email" : "text"}
            />
          </div>
        ))}
      </div>
    </Section>
  );
}

const LLM_PROVIDERS = [
  "local", "anthropic", "openai", "gemini", "copy_paste",
];

function LLMSection() {
  const s = useSection(() => api.settingsGetLLM());
  if (s.data === null) return <Skeleton className="h-32 w-full" />;
  const d = s.data;
  const update = (patch) => s.setData({ ...d, ...patch });
  const save = async () => {
    s.setBusy(true);
    s.setError(null);
    try {
      s.setData(await api.settingsSaveLLM(d));
      s.saveOk();
    } catch (e) {
      s.setError(e.message || String(e));
    } finally {
      s.setBusy(false);
    }
  };
  return (
    <Section
      title="LLM routing"
      onSave={save}
      busy={s.busy}
      error={s.error}
      savedAt={s.savedAt}
    >
      {["evaluation", "resume", "form_filling"].map((fn) => (
        <div key={fn} className="space-y-1">
          <div className="text-xs text-muted-foreground">{fn}</div>
          <div className="flex flex-wrap gap-2">
            {LLM_PROVIDERS.map((p) => (
              <label
                key={p}
                className={
                  "cursor-pointer rounded border px-2 py-1 text-xs " +
                  (d[fn] === p
                    ? "border-accent bg-accent/10"
                    : "border-border")
                }
              >
                <input
                  type="radio"
                  name={`llm-${fn}`}
                  checked={d[fn] === p}
                  onChange={() => update({ [fn]: p })}
                  className="mr-1"
                />
                {p}
              </label>
            ))}
          </div>
        </div>
      ))}
    </Section>
  );
}

function SourcesSection() {
  const s = useSection(() => api.settingsGetSources());
  if (s.data === null) return <Skeleton className="h-40 w-full" />;
  const d = s.data;
  const update = (patch) => s.setData({ ...d, ...patch });
  const save = async () => {
    s.setBusy(true);
    s.setError(null);
    try {
      s.setData(await api.settingsSaveSources(d));
      s.saveOk();
    } catch (e) {
      s.setError(e.message || String(e));
    } finally {
      s.setBusy(false);
    }
  };
  return (
    <Section
      title="Sources"
      onSave={save}
      busy={s.busy}
      error={s.error}
      savedAt={s.savedAt}
    >
      <label className="text-xs text-muted-foreground">
        sources_enabled (one per line)
      </label>
      <textarea
        rows={5}
        value={(d.sources_enabled || []).join("\n")}
        onChange={(e) =>
          update({ sources_enabled: csvSplit(e.target.value) })
        }
        className="w-full rounded border border-border bg-background p-2 text-xs"
      />
      <label className="text-xs text-muted-foreground">JobSpy sites</label>
      <Input
        value={(d.jobspy_sites || []).join(", ")}
        onChange={(e) => update({ jobspy_sites: csvSplit(e.target.value) })}
      />
      <label className="text-xs text-muted-foreground">JobSpy cities</label>
      <Input
        value={(d.jobspy_cities || []).join(", ")}
        onChange={(e) => update({ jobspy_cities: csvSplit(e.target.value) })}
      />
      <label className="text-xs text-muted-foreground">
        LinkedIn keywords (one per line)
      </label>
      <textarea
        rows={4}
        value={(d.linkedin_keywords || []).join("\n")}
        onChange={(e) =>
          update({ linkedin_keywords: csvSplit(e.target.value) })
        }
        className="w-full rounded border border-border bg-background p-2 text-xs"
      />
      <label className="text-xs text-muted-foreground">
        LinkedIn locations
      </label>
      <Input
        value={(d.linkedin_locations || []).join(", ")}
        onChange={(e) =>
          update({ linkedin_locations: csvSplit(e.target.value) })
        }
      />
    </Section>
  );
}

function ExclusionsSection() {
  const s = useSection(() => api.settingsListExclusions());
  const [draft, setDraft] = useState({
    name: "", aliases: "", reason: "",
  });
  if (s.data === null) return <Skeleton className="h-32 w-full" />;
  const entries = s.data.entries || [];
  const add = async () => {
    if (!draft.name.trim()) return;
    s.setBusy(true);
    s.setError(null);
    try {
      const r = await api.settingsAddExclusion({
        name: draft.name.trim(),
        aliases: csvSplit(draft.aliases),
        reason: draft.reason,
        added: new Date().toISOString().slice(0, 10),
      });
      s.setData(r);
      s.saveOk();
      setDraft({ name: "", aliases: "", reason: "" });
    } catch (e) {
      s.setError(e.message || String(e));
    } finally {
      s.setBusy(false);
    }
  };
  const remove = async (name) => {
    s.setBusy(true);
    try {
      s.setData(await api.settingsDeleteExclusion(name));
      s.saveOk();
    } catch (e) {
      s.setError(e.message || String(e));
    } finally {
      s.setBusy(false);
    }
  };
  return (
    <Section
      title="Excluded employers"
      busy={s.busy}
      error={s.error}
      savedAt={s.savedAt}
    >
      <ul className="space-y-1 text-xs">
        {entries.map((e) => (
          <li
            key={e.name}
            className="flex items-start justify-between gap-2 rounded border border-border p-2"
          >
            <div className="min-w-0">
              <div className="font-semibold">{e.name}</div>
              {e.aliases?.length > 0 && (
                <div className="text-muted-foreground">
                  aliases: {e.aliases.join(", ")}
                </div>
              )}
              {e.reason && (
                <div className="text-muted-foreground">{e.reason}</div>
              )}
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => remove(e.name)}
              disabled={s.busy}
            >
              Remove
            </Button>
          </li>
        ))}
      </ul>
      <div className="space-y-2 border-t border-border pt-2">
        <Input
          placeholder="Company name"
          value={draft.name}
          onChange={(e) =>
            setDraft({ ...draft, name: e.target.value })
          }
        />
        <Input
          placeholder="Aliases (comma-separated)"
          value={draft.aliases}
          onChange={(e) =>
            setDraft({ ...draft, aliases: e.target.value })
          }
        />
        <Input
          placeholder="Reason"
          value={draft.reason}
          onChange={(e) =>
            setDraft({ ...draft, reason: e.target.value })
          }
        />
        <Button size="sm" onClick={add} disabled={s.busy || !draft.name}>
          Add exclusion
        </Button>
      </div>
    </Section>
  );
}

function ScheduleSection() {
  const s = useSection(() => api.settingsGetSchedule());
  if (s.data === null) return <Skeleton className="h-24 w-full" />;
  const d = s.data;
  const [time, setTime] = useState(d.schedule_time);
  useEffect(() => setTime(d.schedule_time), [d.schedule_time]);
  const save = async () => {
    s.setBusy(true);
    s.setError(null);
    try {
      const r = await api.settingsSaveSchedule(time);
      if (!r.ok) s.setError(r.detail || "install failed");
      else s.saveOk();
    } catch (e) {
      s.setError(e.message || String(e));
    } finally {
      s.setBusy(false);
    }
  };
  return (
    <Section
      title="Schedule"
      busy={s.busy}
      error={s.error}
      savedAt={s.savedAt}
    >
      <div className="text-xs text-muted-foreground">
        Next run: {d.next_run || "—"}
      </div>
      <Input
        placeholder="02:00"
        value={time}
        onChange={(e) => setTime(e.target.value)}
      />
      <div className="flex justify-end">
        <Button size="sm" onClick={save} disabled={s.busy}>
          Reinstall service
        </Button>
      </div>
    </Section>
  );
}

function GmailSection() {
  const s = useSection(() => api.settingsGetGmail());
  if (s.data === null) return <Skeleton className="h-16 w-full" />;
  const d = s.data;
  return (
    <Section title="Gmail">
      <div className="text-xs">
        Status:{" "}
        {d.configured ? (
          <span className="text-success">Configured</span>
        ) : d.credentials_present ? (
          <span className="text-warning">
            credentials.json uploaded — run `job-agent gmail-auth`
            in a terminal to complete OAuth.
          </span>
        ) : (
          <span className="text-muted-foreground">Not configured</span>
        )}
      </div>
      {d.token_path && (
        <div className="text-[11px] text-muted-foreground">
          Token: {d.token_path}
        </div>
      )}
    </Section>
  );
}

function DangerZoneSection() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const reset = async () => {
    if (!window.confirm("Reset the setup wizard? This deletes setup_state.json.")) return;
    setBusy(true);
    try {
      const r = await api.settingsResetWizard();
      setMsg(r.deleted ? "Wizard reset." : "Nothing to reset.");
    } catch (e) {
      setMsg(`Failed: ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section title="Danger zone">
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={reset} disabled={busy}>
          Reset setup wizard
        </Button>
        <a
          href={api.settingsExportDataUrl()}
          download
          className="rounded border border-border px-2 py-1 text-xs hover:bg-muted/40"
        >
          Export all data (zip)
        </a>
      </div>
      {msg && <div className="text-xs">{msg}</div>}
    </Section>
  );
}

const THEME_OPTIONS = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "system", label: "System" },
];

function ThemeSection() {
  // FIX-5: explicit Light / Dark / System radio group. Writes
  // through ThemeContext.setPreference so the persisted choice and
  // the live data-theme attribute on <html> both update.
  const { preference, setPreference } = useTheme();
  return (
    <Card>
      <CardContent className="space-y-3 p-4 text-sm">
        <div className="flex items-center gap-2">
          <Palette className="h-4 w-4 text-muted-foreground" />
          <span className="font-semibold">Theme</span>
        </div>
        <p className="text-xs text-muted-foreground">
          "System" follows your operating system's light/dark setting.
        </p>
        <div className="flex flex-col gap-2">
          {THEME_OPTIONS.map((opt) => (
            <label
              key={opt.id}
              className="flex cursor-pointer items-center gap-2 text-sm"
            >
              <input
                type="radio"
                name="theme-preference"
                value={opt.id}
                checked={preference === opt.id}
                onChange={() => setPreference(opt.id)}
                className="h-4 w-4 cursor-pointer"
              />
              <span>{opt.label}</span>
            </label>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ActivityLogSection() {
  // FIX-5: surface the Activity Log inside Settings so the user can
  // audit recent agent actions without leaving the page.
  return (
    <Card>
      <CardContent className="space-y-3 p-4 text-sm">
        <div className="flex items-center gap-2">
          <ScrollText className="h-4 w-4 text-muted-foreground" />
          <span className="font-semibold">Activity Log</span>
        </div>
        <ActivityLogViewer />
      </CardContent>
    </Card>
  );
}

export default function Settings() {
  return (
    <div className="space-y-4 p-4">
      <ProfileSection />
      <ApplicantSection />
      <LLMSection />
      <SourcesSection />
      <ExclusionsSection />
      <ScheduleSection />
      <GmailSection />
      <ThemeSection />
      <ActivityLogSection />
      <DangerZoneSection />
    </div>
  );
}
