import { useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const COUNTRY_DEFAULTS = {
  Canada: { jobspy_sites: ["indeed", "google"] },
  US:     { jobspy_sites: ["indeed", "linkedin", "google"] },
  UK:     { jobspy_sites: ["indeed", "google"] },
};

const ALL_SOURCES = [
  "greenhouse_api", "lever_api", "ashby_api", "personio_api",
  "recruitee_api", "workable_api", "workday", "jobspy",
  "linkedin_guest", "job_bank_csv", "manual_entry", "email_monitor",
];

function csvSplit(s) {
  return s
    .split(/\r?\n|,/)
    .map((t) => t.trim())
    .filter(Boolean);
}

export default function SourcesStep({ onContinue, disabled }) {
  const [country, setCountry] = useState("Canada");
  const [enabled, setEnabled] = useState(
    () => new Set(ALL_SOURCES.filter((s) => s !== "email_monitor")),
  );
  const [linkedinKeywords, setLinkedinKeywords] = useState("");
  const [linkedinLocations, setLinkedinLocations] = useState("");
  const [jobspyCities, setJobspyCities] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const toggle = (src) => {
    const next = new Set(enabled);
    if (next.has(src)) next.delete(src);
    else next.add(src);
    setEnabled(next);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.setupSaveSources({
        sources_enabled: Array.from(enabled),
        country,
        jobspy_sites: COUNTRY_DEFAULTS[country]?.jobspy_sites
          || ["indeed", "google"],
        jobspy_cities: csvSplit(jobspyCities),
        linkedin_keywords: csvSplit(linkedinKeywords),
        linkedin_locations: csvSplit(linkedinLocations),
      });
      onContinue({ sources_count: enabled.size });
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 p-6 text-sm">
        <p className="text-muted-foreground">
          Where to discover postings from. ATS APIs (greenhouse,
          lever, ashby, etc.) are public + safe. JobSpy aggregates
          Indeed / Google Jobs. LinkedIn uses the guest API only —
          no auth, scoped to public listings.
        </p>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Country</label>
          <div className="flex gap-2">
            {Object.keys(COUNTRY_DEFAULTS).map((c) => (
              <label
                key={c}
                className={
                  "cursor-pointer rounded border px-2 py-1 text-xs " +
                  (country === c
                    ? "border-accent bg-accent/10"
                    : "border-border")
                }
              >
                <input
                  type="radio"
                  name="country"
                  checked={country === c}
                  onChange={() => setCountry(c)}
                  className="mr-1"
                />
                {c}
              </label>
            ))}
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            Sources enabled
          </label>
          <div className="grid grid-cols-2 gap-1 md:grid-cols-3">
            {ALL_SOURCES.map((src) => (
              <label
                key={src}
                className="flex cursor-pointer items-center gap-1 text-xs"
              >
                <input
                  type="checkbox"
                  checked={enabled.has(src)}
                  onChange={() => toggle(src)}
                  disabled={busy}
                />
                <span>{src}</span>
              </label>
            ))}
          </div>
          <p className="text-[11px] text-muted-foreground">
            email_monitor stays off here — enable in step 7 if you
            connect Gmail.
          </p>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            JobSpy cities (one per line)
          </label>
          <textarea
            value={jobspyCities}
            onChange={(e) => setJobspyCities(e.target.value)}
            rows={3}
            className="w-full rounded border border-border bg-background p-2 text-xs"
            placeholder="toronto&#10;calgary"
            disabled={busy}
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            LinkedIn keywords (one per line)
          </label>
          <textarea
            value={linkedinKeywords}
            onChange={(e) => setLinkedinKeywords(e.target.value)}
            rows={4}
            className="w-full rounded border border-border bg-background p-2 text-xs"
            placeholder='"senior manager"&#10;"program manager"&#10;"delivery manager"'
            disabled={busy}
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            LinkedIn locations (one per line)
          </label>
          <textarea
            value={linkedinLocations}
            onChange={(e) => setLinkedinLocations(e.target.value)}
            rows={3}
            className="w-full rounded border border-border bg-background p-2 text-xs"
            placeholder="Toronto&#10;Calgary"
            disabled={busy}
          />
        </div>

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}

        <div className="flex justify-end">
          <Button onClick={submit} disabled={busy || disabled}>
            {busy ? "Saving…" : "Continue"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
