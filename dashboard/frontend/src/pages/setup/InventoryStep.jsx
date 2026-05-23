import { useEffect, useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const TABS = [
  { id: "paste",  label: "Copy / paste" },
  { id: "upload", label: "Upload resume" },
  { id: "manual", label: "Manual entry" },
];

function PasteTab({ onParsed, busy, setBusy }) {
  const [prompt, setPrompt] = useState("");
  const [response, setResponse] = useState("");
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.setupInventoryPrompt();
        if (!cancelled) setPrompt(r.prompt);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 2200);
    } catch {
      // Fallback: user can copy from the textarea below.
    }
  };

  const parseResponse = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.setupParseInventoryResponse(response);
      onParsed({ markdown: response, ...r });
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 text-sm">
      <p className="text-muted-foreground">
        Paste this prompt into any AI assistant (Claude, ChatGPT,
        Gemini). Run the interview to completion. Paste the final
        markdown response back into the bottom field.
      </p>

      <div className="space-y-2">
        <div className="flex items-baseline justify-between">
          <span className="text-xs font-medium">Prompt</span>
          <Button
            size="sm"
            variant="outline"
            onClick={copyPrompt}
            disabled={!prompt}
          >
            {copied ? "Copied" : "Copy prompt"}
          </Button>
        </div>
        <textarea
          value={prompt}
          readOnly
          rows={8}
          className="w-full rounded border border-border bg-muted/30 p-2 font-mono text-xs"
        />
      </div>

      <div className="space-y-2">
        <span className="text-xs font-medium">Paste response</span>
        <textarea
          value={response}
          onChange={(e) => setResponse(e.target.value)}
          rows={12}
          placeholder="Paste the AI's final markdown inventory here…"
          className="w-full rounded border border-border bg-background p-2 font-mono text-xs"
          disabled={busy}
        />
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex justify-end">
        <Button
          onClick={parseResponse}
          disabled={!response.trim() || busy}
        >
          {busy ? "Parsing…" : "Parse + score"}
        </Button>
      </div>
    </div>
  );
}

function UploadTab({ onParsed, busy, setBusy }) {
  const [file, setFile] = useState(null);
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(null);

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.setupParseResume(file);
      setPreview(r);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const useParsedRaw = () => {
    // Resume parse is rough — it's a starting point, not a final
    // inventory. The user typically still copy-pastes through an
    // AI to convert resume bullets into the inventory narrative.
    // We hand the raw text back so the user can iterate.
    onParsed({
      markdown: `# Career Inventory (from resume)\n\n${preview.raw_text}`,
      roles: preview.roles.map((r) => ({
        ...r, body: "", body_chars: 0,
      })),
      quality_score: 0,
      quality_notes: [
        "Resume text imported as-is. Refine via the Copy/paste " +
          "tab to turn each role into a detailed inventory entry.",
      ],
    });
  };

  return (
    <div className="space-y-4 text-sm">
      <p className="text-muted-foreground">
        Upload a .docx resume. We'll extract role title / company /
        dates lines as a starting scaffold. The full inventory still
        needs the Copy/paste interview step to add daily-work
        narrative.
      </p>
      <Input
        type="file"
        accept=".docx,.txt,.md"
        onChange={(e) => setFile(e.target.files?.[0] || null)}
        disabled={busy}
      />
      <Button onClick={upload} disabled={!file || busy}>
        {busy ? "Parsing…" : "Parse resume"}
      </Button>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
          {error}
        </div>
      )}

      {preview && (
        <div className="space-y-2 rounded-md border border-border bg-muted/30 p-3">
          <div className="text-xs font-medium">
            Parsed {preview.roles.length} role(s)
          </div>
          <ul className="space-y-1 text-xs">
            {preview.roles.map((r, i) => (
              <li key={i}>
                <strong>{r.title}</strong> — {r.company} — {r.dates}
              </li>
            ))}
          </ul>
          <Button size="sm" onClick={useParsedRaw}>
            Use as starting point
          </Button>
        </div>
      )}
    </div>
  );
}

function ManualTab({ onParsed, busy, setBusy }) {
  const [roles, setRoles] = useState([emptyRole()]);
  const [error, setError] = useState(null);

  function emptyRole() {
    return {
      title: "",
      company: "",
      dates: "",
      location: "",
      narrative: "",
      tools: "",
      outcomes: "",
    };
  }

  const updateRole = (i, field, value) => {
    const next = [...roles];
    next[i] = { ...next[i], [field]: value };
    setRoles(next);
  };

  const addRole = () => setRoles([...roles, emptyRole()]);

  const renderMarkdown = (rs) => {
    const blocks = ["# Career Inventory", "", "## Roles", ""];
    for (const r of rs) {
      blocks.push(
        `### ${r.title || "?"} — ${r.company || "?"} — ${r.dates || "?"}`,
      );
      if (r.location) blocks.push(`**Location:** ${r.location}`);
      blocks.push("");
      if (r.narrative) blocks.push(r.narrative, "");
      if (r.tools) {
        blocks.push("**Tools / technologies daily:**");
        blocks.push(r.tools, "");
      }
      if (r.outcomes) {
        blocks.push("**Measurable outcomes:**");
        blocks.push(r.outcomes, "");
      }
      blocks.push("---", "");
    }
    return blocks.join("\n");
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const md = renderMarkdown(roles);
      const parsed = await api.setupParseInventoryResponse(md);
      onParsed({ markdown: md, ...parsed });
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 text-sm">
      <p className="text-muted-foreground">
        Most tedious option but works without any AI integration.
        Add one block per role; fill in as much detail as you can.
      </p>
      {roles.map((r, i) => (
        <Card key={i} className="p-3">
          <div className="grid grid-cols-1 gap-2 text-xs md:grid-cols-3">
            <Input
              placeholder="Title"
              value={r.title}
              onChange={(e) => updateRole(i, "title", e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder="Company"
              value={r.company}
              onChange={(e) => updateRole(i, "company", e.target.value)}
              disabled={busy}
            />
            <Input
              placeholder="Dates (e.g. Jun 2020 – Mar 2023)"
              value={r.dates}
              onChange={(e) => updateRole(i, "dates", e.target.value)}
              disabled={busy}
            />
          </div>
          <Input
            placeholder="Location"
            value={r.location}
            onChange={(e) => updateRole(i, "location", e.target.value)}
            disabled={busy}
            className="mt-2"
          />
          <textarea
            placeholder="Daily work narrative — what you actually did"
            value={r.narrative}
            onChange={(e) => updateRole(i, "narrative", e.target.value)}
            rows={4}
            className="mt-2 w-full rounded border border-border bg-background p-2 text-xs"
            disabled={busy}
          />
          <textarea
            placeholder="Tools (one per line)"
            value={r.tools}
            onChange={(e) => updateRole(i, "tools", e.target.value)}
            rows={2}
            className="mt-2 w-full rounded border border-border bg-background p-2 text-xs"
            disabled={busy}
          />
          <textarea
            placeholder="Measurable outcomes (one per line)"
            value={r.outcomes}
            onChange={(e) => updateRole(i, "outcomes", e.target.value)}
            rows={2}
            className="mt-2 w-full rounded border border-border bg-background p-2 text-xs"
            disabled={busy}
          />
        </Card>
      ))}
      <Button variant="outline" onClick={addRole} disabled={busy}>
        Add another role
      </Button>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex justify-end">
        <Button onClick={submit} disabled={busy}>
          {busy ? "Saving…" : "Parse + score"}
        </Button>
      </div>
    </div>
  );
}

export default function InventoryStep({ onContinue, disabled }) {
  const [tab, setTab] = useState("paste");
  const [parsed, setParsed] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saving, setSaving] = useState(false);

  const saveAndContinue = async () => {
    if (!parsed) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.setupSaveInventory(parsed.markdown);
      onContinue({
        role_count: parsed.roles.length,
        quality_score: parsed.quality_score,
      });
    } catch (e) {
      setSaveError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 p-6 text-sm">
        <div className="flex gap-2">
          {TABS.map((t) => (
            <Button
              key={t.id}
              size="sm"
              variant={tab === t.id ? "default" : "outline"}
              onClick={() => {
                setTab(t.id);
                setParsed(null);
              }}
              disabled={busy || saving}
            >
              {t.label}
            </Button>
          ))}
        </div>

        {tab === "paste" && (
          <PasteTab
            onParsed={setParsed}
            busy={busy}
            setBusy={setBusy}
          />
        )}
        {tab === "upload" && (
          <UploadTab
            onParsed={setParsed}
            busy={busy}
            setBusy={setBusy}
          />
        )}
        {tab === "manual" && (
          <ManualTab
            onParsed={setParsed}
            busy={busy}
            setBusy={setBusy}
          />
        )}

        {parsed && (
          <div className="space-y-2 rounded-md border border-border bg-muted/30 p-3">
            <div className="flex items-baseline justify-between">
              <div className="text-xs font-medium">Quality preview</div>
              <div className="text-lg font-semibold">
                {parsed.quality_score}/100
              </div>
            </div>
            <div className="text-xs text-muted-foreground">
              {parsed.roles.length} role(s) detected
            </div>
            {parsed.quality_notes.length > 0 && (
              <ul className="space-y-1 text-xs text-warning">
                {parsed.quality_notes.map((n, i) => (
                  <li key={i}>• {n}</li>
                ))}
              </ul>
            )}
            {saveError && (
              <div className="text-xs text-destructive">{saveError}</div>
            )}
            <div className="flex justify-end gap-2">
              <Button
                onClick={saveAndContinue}
                disabled={saving || disabled || parsed.roles.length === 0}
              >
                {saving ? "Saving…" : "Save + continue"}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
