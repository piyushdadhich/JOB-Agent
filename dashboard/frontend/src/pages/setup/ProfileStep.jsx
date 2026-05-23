import { useState } from "react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function ProfileStep({ onContinue, disabled }) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      // Persists default.yaml + default_applicant.yaml from the
      // .example templates with these fields patched in.
      await api.setupSaveProfile({
        full_name: fullName,
        email,
        phone,
        linkedin_url: linkedin,
      });
      onContinue({ full_name: fullName, email });
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const valid = fullName.trim() && email.trim();

  return (
    <Card>
      <CardContent className="space-y-4 p-6 text-sm">
        <p className="text-muted-foreground">
          We use these on every application. They're written to your
          gitignored applicant YAML — nothing leaves your machine.
        </p>

        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Full name *</label>
            <Input
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Alex Doe"
              disabled={busy || disabled}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Email *</label>
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              disabled={busy || disabled}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Phone</label>
            <Input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="555-000-0000"
              disabled={busy || disabled}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">
              LinkedIn URL
            </label>
            <Input
              value={linkedin}
              onChange={(e) => setLinkedin(e.target.value)}
              placeholder="https://linkedin.com/in/yourname"
              disabled={busy || disabled}
            />
          </div>
        </div>

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            {error}
          </div>
        )}

        <div className="flex justify-end">
          <Button onClick={submit} disabled={!valid || busy || disabled}>
            {busy ? "Saving…" : "Continue"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
