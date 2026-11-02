"use client";

import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { fetchProfile, saveProfile } from "@/lib/api";

export function ProfileTab() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [savedName, setSavedName] = useState("");
  const [savedEmail, setSavedEmail] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const profile = await fetchProfile();
        if (cancelled) return;
        const nextName = profile.display_name ?? "";
        const nextEmail = profile.email ?? "";
        setName(nextName);
        setEmail(nextEmail);
        setSavedName(nextName);
        setSavedEmail(nextEmail);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load profile."
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSave() {
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const profile = await saveProfile({
        display_name: name.trim(),
        email: email.trim(),
      });
      const nextName = profile.display_name ?? "";
      const nextEmail = profile.email ?? "";
      setName(nextName);
      setEmail(nextEmail);
      setSavedName(nextName);
      setSavedEmail(nextEmail);
      setNotice("Profile saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save profile.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="pp-page-title">
        <h1>Profile</h1>
        <p>
          Your name and work email. PerfPilot uses this profile across all of
          your personal access tokens — it should match the identity those
          tokens were created for.
        </p>
      </div>

      <div className="pp-note">
        <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <span>
          Saved on the server for this browser identity. A later release will
          replace this with your Microsoft Entra ID sign-in.
        </span>
      </div>

      {error && (
        <div className="pp-note" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="pp-card">
        <div className="pp-card-header">
          <div>
            <h3>Your profile</h3>
            <p>Display name and email for this browser session.</p>
          </div>
        </div>
        <div className="pp-card-body">
          <div className="pp-modal-field">
            <label className="pp-field-label" htmlFor="profile-name">
              Display name
            </label>
            <input
              className="pp-field"
              id="profile-name"
              type="text"
              placeholder="e.g. Jane Doe"
              autoComplete="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="pp-modal-field" style={{ marginBottom: 0 }}>
            <label className="pp-field-label" htmlFor="profile-email">
              Work email
            </label>
            <input
              className="pp-field"
              id="profile-email"
              type="email"
              placeholder="jane.doe@example.com"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
        </div>
        <div
          className="pp-card-footer"
          style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "14px 20px" }}
        >
          {notice && (
            <span className="mr-auto self-center text-[12px]">{notice}</span>
          )}
          <button
            type="button"
            className="pp-btn pp-btn-secondary"
            disabled={busy}
            onClick={() => {
              setName(savedName);
              setEmail(savedEmail);
              setNotice(null);
              setError(null);
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            className="pp-btn pp-btn-primary"
            disabled={busy}
            onClick={() => void handleSave()}
          >
            Save profile
          </button>
        </div>
      </div>
    </div>
  );
}
