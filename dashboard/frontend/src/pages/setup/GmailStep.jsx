import { useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export default function GmailStep({ onContinue, disabled }) {
  const [mode, setMode] = useState(null); // null | "connect" | "skip"
  const [file, setFile] = useState(null);
  const [uploaded, setUploaded] = useState(null);
  const [authorized, setAuthorized] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.setupGmailUpload(file);
      setUploaded(r);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const check = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.setupGmailFinalize();
      setAuthorized(r);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const skip = () =>
    onContinue({ gmail_configured: false, skipped: true });

  const finishWithConnection = () =>
    onContinue({ gmail_configured: true });

  if (mode === null) {
    return (
      <Card>
        <CardContent className="space-y-4 p-6 text-sm">
          <p className="text-muted-foreground">
            Optional. Connect Gmail (read-only) so the email-monitor
            source can parse recruiter and newsletter postings into
            your shortlist. You can skip and add this later.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={skip} disabled={disabled}>
              Skip
            </Button>
            <Button onClick={() => setMode("connect")} disabled={disabled}>
              Connect Gmail
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="space-y-4 p-6 text-sm">
        <p className="text-muted-foreground">
          Two steps:
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-xs">
          <li>
            Create an OAuth client in Google Cloud Console (
            <a
              href="https://console.cloud.google.com/apis/credentials"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              console.cloud.google.com/apis/credentials
            </a>
            ). Download <code>credentials.json</code>.
          </li>
          <li>Upload it below; we'll save it to <code>data/{`{profile}`}/gmail_credentials.json</code>.</li>
        </ol>

        <div className="space-y-2">
          <input
            type="file"
            accept=".json,application/json"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            disabled={busy}
          />
          <Button onClick={upload} disabled={!file || busy}>
            Upload credentials.json
          </Button>
          {uploaded && (
            <div className="text-xs text-success">
              Saved to {uploaded.saved_to}.
            </div>
          )}
        </div>

        {uploaded && (
          <div className="space-y-2 rounded-md border border-border bg-muted/30 p-3">
            <p className="text-xs">
              Now run the OAuth flow in a terminal:
            </p>
            <pre className="rounded bg-background p-2 text-xs">
              job-agent gmail-auth --profile default
            </pre>
            <p className="text-xs text-muted-foreground">
              Your browser will open the consent screen. Approve the
              read-only scope. The agent saves a refresh token to
              <code> data/{`{profile}`}/gmail_token.json</code>.
            </p>
            <Button size="sm" onClick={check} disabled={busy} variant="outline">
              I've authorized — check status
            </Button>
            {authorized && authorized.gmail_configured && (
              <div className="text-xs text-success">
                Token detected at {authorized.token_path}.
              </div>
            )}
            {authorized && !authorized.gmail_configured && (
              <div className="text-xs text-warning">
                Token not found yet. Re-run the command, then check
                again.
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={skip} disabled={busy || disabled}>
            Skip for now
          </Button>
          <Button
            onClick={finishWithConnection}
            disabled={
              !authorized || !authorized.gmail_configured || busy || disabled
            }
          >
            Continue
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
