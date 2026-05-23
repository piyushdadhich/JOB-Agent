import { useState } from "react";
import { AlertCircle, Loader2, Plus } from "lucide-react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group";

// Add a job the user found outside the discovery pipeline.
// `onAdded(result)` fires after a successful POST so the caller can
// refresh its board / list without a full page reload.
export default function AddJobModal({ open, onOpenChange, onAdded }) {
  const [mode, setMode] = useState("url");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [employer, setEmployer] = useState("");
  const [location, setLocation] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [postingText, setPostingText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  function reset() {
    setUrl("");
    setTitle("");
    setEmployer("");
    setLocation("");
    setSourceUrl("");
    setPostingText("");
    setError(null);
    setBusy(false);
  }

  function close() {
    reset();
    onOpenChange(false);
  }

  async function submit() {
    setError(null);
    let payload;
    if (mode === "url") {
      if (!url.trim()) {
        setError("Enter a job URL.");
        return;
      }
      payload = { url: url.trim() };
    } else {
      if (!title.trim() || !employer.trim()) {
        setError("Job title and company are required.");
        return;
      }
      payload = {
        title: title.trim(),
        employer: employer.trim(),
        location: location.trim() || null,
        source_url: sourceUrl.trim() || null,
        posting_text: postingText.trim() || null,
      };
    }
    setBusy(true);
    try {
      const result = await api.applicationsAddJob(payload);
      onAdded?.(result);
      close();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="max-w-lg p-0">
        <DialogHeader className="border-b border-border p-4">
          <DialogTitle>Add a Job</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 p-4">
          <ToggleGroup
            type="single"
            value={mode}
            onValueChange={(v) => v && setMode(v)}
            aria-label="Entry mode"
          >
            <ToggleGroupItem value="url">Paste URL</ToggleGroupItem>
            <ToggleGroupItem value="manual">
              Enter manually
            </ToggleGroupItem>
          </ToggleGroup>

          {mode === "url" ? (
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                Job URL
              </label>
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://…"
                spellCheck={false}
              />
              <p className="text-[11px] text-muted-foreground">
                We fetch the page and extract the title, company, and
                description. Switch to manual entry if the page can't
                be read.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <Field label="Job Title *">
                <Input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Project Manager"
                />
              </Field>
              <Field label="Company *">
                <Input
                  value={employer}
                  onChange={(e) => setEmployer(e.target.value)}
                  placeholder="Acme Corp"
                />
              </Field>
              <Field label="Location">
                <Input
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="City, State"
                />
              </Field>
              <Field label="URL (optional)">
                <Input
                  value={sourceUrl}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  placeholder="https://…"
                  spellCheck={false}
                />
              </Field>
              <Field label="Description">
                <Textarea
                  value={postingText}
                  onChange={(e) => setPostingText(e.target.value)}
                  placeholder="Paste the full job description here…"
                  spellCheck={false}
                  className="min-h-[120px] text-xs"
                />
              </Field>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-border p-3">
          <Button variant="outline" onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button variant="accent" onClick={submit} disabled={busy}>
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Plus className="h-3 w-3" />
            )}
            {mode === "url" ? "Fetch & Add" : "Add to Applications"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}
