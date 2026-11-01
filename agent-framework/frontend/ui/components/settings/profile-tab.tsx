"use client";

import { useState } from "react";
import { Info } from "lucide-react";

export function ProfileTab() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <div>
      <div className="pp-page-title">
        <h1>Profile</h1>
        <p>
          Your name and work email. For now, PerfPilot uses this profile across
          all of your personal access tokens — it should match the identity
          those tokens were created for.
        </p>
      </div>

      <div className="pp-note">
        <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <span>
          Temporary: in a future release this will come from your Microsoft
          Entra ID sign-in and will be read-only here. This form is a visual
          shell and is not saved to a server.
        </span>
      </div>

      <div className="pp-card">
        <div className="pp-card-header">
          <div>
            <h3>Your profile</h3>
            <p>Stored on this device only until Entra ID integration lands.</p>
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
            onClick={() => {
              setName("");
              setEmail("");
              setNotice(null);
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            className="pp-btn pp-btn-primary"
            onClick={() =>
              setNotice("Preview only — nothing was saved to a server.")
            }
          >
            Save profile
          </button>
        </div>
      </div>
    </div>
  );
}
