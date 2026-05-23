import { useEffect, useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const FUNCTIONS = [
  {
    id: "evaluation",
    label: "Evaluation",
    blurb:
      "Scoring postings against your inventory. The hottest path " +
      "— runs on every new posting every day.",
  },
  {
    id: "resume",
    label: "Resume generation",
    blurb:
      "Tailoring resume + cover-letter drafts for selected postings. " +
      "Quality matters more than speed here.",
  },
  {
    id: "form_filling",
    label: "Form filling",
    blurb:
      "Mapping free-text screening questions onto your profile. " +
      "Local models work well for most fields.",
  },
];

const PROVIDERS = [
  { id: "local",     label: "Local (Ollama)" },
  { id: "anthropic", label: "Anthropic" },
  { id: "openai",    label: "OpenAI" },
  { id: "gemini",    label: "Gemini" },
  { id: "copy_paste", label: "Copy / paste" },
];

function defaultProviderForTier(tier) {
  if (tier === "full") return "local";
  if (tier === "recommended") return "local";
  return "gemini";
}

export default function LLMConfigStep({
  hardwareTier,
  onContinue,
  disabled,
}) {
  const [routing, setRouting] = useState(() => {
    const def = defaultProviderForTier(hardwareTier);
    return Object.fromEntries(FUNCTIONS.map((f) => [f.id, def]));
  });
  const [apiKeys, setApiKeys] = useState({
    anthropic: "",
    openai: "",
    gemini: "",
  });
  const [keyStatus, setKeyStatus] = useState({});
  const [ollama, setOllama] = useState(null);
  const [busy, setBusy] = useState(false);

  // Probe Ollama once on mount so the form shows whether the
  // local path is realistic on this host.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.setupCheckOllama();
        if (!cancelled) setOllama(r);
      } catch {
        if (!cancelled) setOllama({ installed: false, models: [] });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const usedProviders = new Set(Object.values(routing));
  const needsKey = ["anthropic", "openai", "gemini"].filter((p) =>
    usedProviders.has(p),
  );

  const testKey = async (provider) => {
    setBusy(true);
    try {
      const r = await api.setupTestApiKey(provider, apiKeys[provider]);
      setKeyStatus({ ...keyStatus, [provider]: r });
    } catch (e) {
      setKeyStatus({
        ...keyStatus,
        [provider]: { ok: false, detail: e.message || String(e) },
      });
    } finally {
      setBusy(false);
    }
  };

  const continueDisabled =
    disabled ||
    busy ||
    needsKey.some(
      (p) => !apiKeys[p].trim() || !keyStatus[p] || !keyStatus[p].ok,
    );

  return (
    <Card>
      <CardContent className="space-y-5 p-6 text-sm">
        <p className="text-muted-foreground">
          Pick a model source for each task. Defaults match your
          hardware tier ({hardwareTier || "unknown"}).
        </p>

        {/* Per-function routing */}
        <div className="space-y-4">
          {FUNCTIONS.map((fn) => (
            <div key={fn.id} className="space-y-1">
              <div className="flex items-baseline justify-between">
                <div className="font-medium">{fn.label}</div>
              </div>
              <div className="text-xs text-muted-foreground">{fn.blurb}</div>
              <div className="flex flex-wrap gap-2 pt-1">
                {PROVIDERS.map((p) => (
                  <label
                    key={p.id}
                    className={
                      "cursor-pointer rounded border px-2 py-1 text-xs " +
                      (routing[fn.id] === p.id
                        ? "border-accent bg-accent/10"
                        : "border-border")
                    }
                  >
                    <input
                      type="radio"
                      name={`route-${fn.id}`}
                      checked={routing[fn.id] === p.id}
                      onChange={() =>
                        setRouting({ ...routing, [fn.id]: p.id })
                      }
                      className="mr-1"
                    />
                    {p.label}
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Ollama status */}
        {usedProviders.has("local") && (
          <div className="rounded-md border border-border bg-muted/30 p-3 text-xs">
            <div className="font-medium">Ollama</div>
            {ollama === null ? (
              <div className="text-muted-foreground">Probing…</div>
            ) : ollama.installed ? (
              <div className="text-muted-foreground">
                Installed ·{" "}
                {ollama.models.length > 0
                  ? `${ollama.models.length} model(s) available`
                  : "no models pulled yet"}
                {ollama.detail ? ` · ${ollama.detail}` : null}
              </div>
            ) : (
              <div className="text-warning">
                Ollama not detected. Install from{" "}
                <a
                  href="https://ollama.com/download"
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  ollama.com/download
                </a>{" "}
                or switch the affected functions to an API provider.
              </div>
            )}
          </div>
        )}

        {/* API key inputs (only for providers actually selected) */}
        {needsKey.length > 0 && (
          <div className="space-y-3">
            <div className="font-medium">API keys</div>
            {needsKey.map((p) => (
              <div key={p} className="space-y-1">
                <div className="text-xs font-medium capitalize">{p}</div>
                <div className="flex gap-2">
                  <Input
                    type="password"
                    placeholder={`Paste your ${p} API key`}
                    value={apiKeys[p]}
                    onChange={(e) =>
                      setApiKeys({ ...apiKeys, [p]: e.target.value })
                    }
                    disabled={busy}
                  />
                  <Button
                    onClick={() => testKey(p)}
                    disabled={busy || !apiKeys[p].trim()}
                    variant="outline"
                  >
                    Test
                  </Button>
                </div>
                {keyStatus[p] && (
                  <div
                    className={
                      "text-xs " +
                      (keyStatus[p].ok ? "text-success" : "text-destructive")
                    }
                  >
                    {keyStatus[p].ok
                      ? "Key validated."
                      : `Key failed: ${keyStatus[p].detail || "unknown"}`}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="flex justify-end">
          <Button
            onClick={() =>
              onContinue({
                routing,
                // Saved separately by Spec 1 TASK 3 once the
                // profile YAML lands — for now we just record
                // which providers the user chose so step 3 can
                // surface the same form populated.
                providers_in_use: Array.from(usedProviders),
              })
            }
            disabled={continueDisabled}
          >
            Continue
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
