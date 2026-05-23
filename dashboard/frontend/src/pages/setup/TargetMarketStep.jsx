import { useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const SIZE_OPTIONS = ["small", "mid_sized", "large", "enterprise"];
const REMOTE_OPTIONS = ["remote", "hybrid", "onsite", "any"];

function csvSplit(s) {
  return s
    .split(/\r?\n|,/)
    .map((t) => t.trim())
    .filter(Boolean);
}

export default function TargetMarketStep({ onContinue, disabled }) {
  const [cities, setCities] = useState("");
  const [remote, setRemote] = useState("any");
  const [floor, setFloor] = useState("");
  const [target, setTarget] = useState("");
  const [cap, setCap] = useState("");
  const [roleTypes, setRoleTypes] = useState("");
  const [size, setSize] = useState("mid_sized");
  const [industries, setIndustries] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.setupSaveTargetMarket({
        target_cities: csvSplit(cities),
        remote_preference: remote,
        salary: {
          floor: Number(floor) || 0,
          target: Number(target) || 0,
          cap: Number(cap) || 0,
        },
        target_role_types: csvSplit(roleTypes),
        employer_size_priority: size,
        industries: csvSplit(industries),
      });
      onContinue({ city_count: csvSplit(cities).length });
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
          Where + what you're targeting. Used by the discovery
          sources (LinkedIn, JobSpy, ATS feeds) to scope the search.
        </p>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            Cities (one per line or comma-separated)
          </label>
          <textarea
            value={cities}
            onChange={(e) => setCities(e.target.value)}
            rows={3}
            className="w-full rounded border border-border bg-background p-2 text-xs"
            placeholder="toronto&#10;mississauga&#10;remote_canada"
            disabled={busy}
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            Remote preference
          </label>
          <div className="flex flex-wrap gap-2">
            {REMOTE_OPTIONS.map((opt) => (
              <label
                key={opt}
                className={
                  "cursor-pointer rounded border px-2 py-1 text-xs " +
                  (remote === opt
                    ? "border-accent bg-accent/10"
                    : "border-border")
                }
              >
                <input
                  type="radio"
                  name="remote"
                  checked={remote === opt}
                  onChange={() => setRemote(opt)}
                  className="mr-1"
                />
                {opt}
              </label>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">
              Salary floor
            </label>
            <Input
              type="number"
              value={floor}
              onChange={(e) => setFloor(e.target.value)}
              placeholder="80000"
              disabled={busy}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Target</label>
            <Input
              type="number"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="95000"
              disabled={busy}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Cap</label>
            <Input
              type="number"
              value={cap}
              onChange={(e) => setCap(e.target.value)}
              placeholder="120000"
              disabled={busy}
            />
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            Role types (one per line)
          </label>
          <textarea
            value={roleTypes}
            onChange={(e) => setRoleTypes(e.target.value)}
            rows={3}
            className="w-full rounded border border-border bg-background p-2 text-xs"
            placeholder="delivery_manager&#10;program_manager&#10;business_analyst"
            disabled={busy}
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            Preferred employer size
          </label>
          <div className="flex flex-wrap gap-2">
            {SIZE_OPTIONS.map((opt) => (
              <label
                key={opt}
                className={
                  "cursor-pointer rounded border px-2 py-1 text-xs " +
                  (size === opt
                    ? "border-accent bg-accent/10"
                    : "border-border")
                }
              >
                <input
                  type="radio"
                  name="size"
                  checked={size === opt}
                  onChange={() => setSize(opt)}
                  className="mr-1"
                />
                {opt.replace(/_/g, " ")}
              </label>
            ))}
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">
            Industries (one per line)
          </label>
          <textarea
            value={industries}
            onChange={(e) => setIndustries(e.target.value)}
            rows={3}
            className="w-full rounded border border-border bg-background p-2 text-xs"
            placeholder="public_sector&#10;financial_services&#10;healthcare_education"
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
