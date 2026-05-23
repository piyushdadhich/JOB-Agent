import { useEffect, useState } from "react";
import { Copy, Check, FileText, Download, Eye, Loader2, AlertCircle } from "lucide-react";
import { api } from "@/api";
import MarkdownPreview from "./MarkdownPreview.jsx";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// One panel per document type ("resume" or "cover-letter").
export default function PromptPanel({ postingId, kind, savedPrompt, savedText, onSaved }) {
  const [prompt, setPrompt] = useState(savedPrompt || null);
  const [paste, setPaste] = useState(savedText || "");
  const [savingState, setSavingState] = useState("idle"); // idle|saving|saved|error
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  const label = kind === "resume" ? "Resume" : "Cover letter";
  const promptFn =
    kind === "resume" ? api.getResumePrompt : api.getCoverLetterPrompt;
  const saveFn =
    kind === "resume" ? api.saveResumeText : api.saveCoverLetterText;

  useEffect(() => {
    if (savedPrompt) {
      setPrompt(savedPrompt);
      return;
    }
    let cancelled = false;
    promptFn(postingId)
      .then((r) => {
        if (!cancelled) setPrompt(r.prompt);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [postingId, kind, savedPrompt]);

  useEffect(() => {
    setPaste(savedText || "");
    setSavingState(savedText ? "saved" : "idle");
  }, [postingId, kind, savedText]);

  async function copyPrompt() {
    if (!prompt) return;
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      setError(`Clipboard write failed: ${e.message}`);
    }
  }

  async function save() {
    if (!paste.trim()) {
      setError("Paste Claude's response before saving.");
      return;
    }
    setSavingState("saving");
    setError(null);
    try {
      const r = await saveFn(postingId, paste);
      setSavingState("saved");
      onSaved?.(r);
    } catch (e) {
      setSavingState("error");
      setError(String(e));
    }
  }

  function preview() {
    window.open(
      `/api/preview/${postingId}/${kind}.docx`,
      "_blank",
      "noopener",
    );
  }

  function download() {
    window.location.href = `/api/download/${postingId}/${kind}.docx`;
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-muted-foreground" />
          {label}
        </CardTitle>
        {savingState === "saved" && (
          <Badge variant="success">
            <Check className="h-3 w-3" />
            Saved
          </Badge>
        )}
        {savingState === "saving" && (
          <Badge variant="muted">
            <Loader2 className="h-3 w-3 animate-spin" />
            Saving…
          </Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Prompt block + copy button */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Prompt
            </span>
            <Tooltip open={copied || undefined}>
              <TooltipTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={copyPrompt}
                  disabled={!prompt}
                >
                  {copied ? (
                    <Check className="h-3 w-3" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                  {copied ? "Copied" : "Copy"}
                </Button>
              </TooltipTrigger>
              <TooltipContent>Copied to clipboard</TooltipContent>
            </Tooltip>
          </div>
          <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">
            {prompt ?? "Loading prompt…"}
          </pre>
        </div>

        {/* Paste + live preview side-by-side */}
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="space-y-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Paste Claude's response
            </span>
            <Textarea
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
              placeholder="Paste Claude's markdown response here…"
              spellCheck={false}
              className="min-h-[280px] font-mono text-xs"
            />
          </div>
          <div className="space-y-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Live preview
            </span>
            <MarkdownPreview source={paste} />
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            variant="accent"
            size="sm"
            onClick={save}
            disabled={savingState === "saving" || !paste.trim()}
          >
            <Check className="h-3 w-3" />
            Save {label.toLowerCase()}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={preview}
            disabled={savingState !== "saved"}
          >
            <Eye className="h-3 w-3" />
            Preview .docx
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={download}
            disabled={savingState !== "saved"}
          >
            <Download className="h-3 w-3" />
            Download .docx
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
