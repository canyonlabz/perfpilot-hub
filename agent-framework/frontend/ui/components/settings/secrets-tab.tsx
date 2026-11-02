"use client";

import { useEffect, useState } from "react";
import { Info, Plus, Trash2, X } from "lucide-react";
import {
  COMPONENT_LABELS,
  COMPONENT_VALUE_FIELDS,
  FOCUS_AREAS,
  focusAreaById,
  type FocusAreaId,
  type SecretComponent,
  type SettingsSecret,
} from "@/lib/settings-catalog";
import { createSecret, deleteSecret } from "@/lib/api";

type ModalState = {
  sectionId: FocusAreaId;
};

export function SecretsTab({
  secrets,
  onSecretsChange,
  onReload,
}: {
  secrets: SettingsSecret[];
  onSecretsChange: (secrets: SettingsSecret[]) => void;
  onReload: () => Promise<void>;
}) {
  const [modal, setModal] = useState<ModalState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function handleDelete(secret: SettingsSecret) {
    if (
      !window.confirm(
        `Remove “${secret.name}”? Project bindings that used this name will be cleared.`
      )
    ) {
      return;
    }
    setBusyId(secret.id);
    setError(null);
    try {
      await deleteSecret(secret.id);
      onSecretsChange(secrets.filter((row) => row.id !== secret.id));
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete secret.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <div className="pp-page-title">
        <h1>Secrets</h1>
        <p>
          Add personal access tokens, API keys, and credentials grouped by
          performance-testing focus area. Each secret has a user-chosen name,
          a value, and a component it applies to.
        </p>
      </div>

      <div className="pp-note">
        <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <span>
          Values are sent once and stored encrypted. This page only shows
          names, components, and a mask — never the saved value.
        </span>
      </div>

      {error && (
        <div className="pp-note" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {FOCUS_AREAS.map((section) => {
        const rows = secrets.filter((secret) => secret.sectionId === section.id);
        return (
          <div key={section.id} className="pp-card">
            <div className="pp-card-header">
              <div>
                <h3>{section.title}</h3>
                <p>{section.hint}</p>
              </div>
              <button
                type="button"
                className="pp-btn pp-btn-secondary"
                onClick={() => setModal({ sectionId: section.id })}
              >
                <Plus className="h-3 w-3" />
                Add secret
              </button>
            </div>
            <div className="pp-card-body pp-card-body-tight">
              {rows.length === 0 ? (
                <div className="pp-section-empty">
                  No secrets added yet. Click <strong>Add secret</strong> to
                  create one.
                </div>
              ) : (
                rows.map((secret) => (
                  <div key={secret.id} className="pp-secret-item">
                    <div className="font-medium">{secret.name}</div>
                    <span className="pp-component-pill">
                      <span
                        className="pp-component-dot"
                        data-component={secret.component}
                      />
                      {COMPONENT_LABELS[secret.component]}
                    </span>
                    <span className="pp-masked">{secret.masked}</span>
                    <span className="text-[11.5px] text-[var(--text-subtle)]">
                      {secret.added}
                    </span>
                    <button
                      type="button"
                      className="pp-icon-btn pp-secret-delete"
                      title={`Delete ${secret.name}`}
                      aria-label={`Delete ${secret.name}`}
                      disabled={busyId === secret.id}
                      onClick={() => void handleDelete(secret)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>
        );
      })}

      {modal && (
        <AddSecretModal
          sectionId={modal.sectionId}
          onClose={() => setModal(null)}
          onSaved={async (entry) => {
            onSecretsChange([...secrets, entry]);
            setModal(null);
            await onReload();
          }}
          onError={setError}
        />
      )}
    </div>
  );
}

function AddSecretModal({
  sectionId,
  onClose,
  onSaved,
  onError,
}: {
  sectionId: FocusAreaId;
  onClose: () => void;
  onSaved: (secret: SettingsSecret) => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const section = focusAreaById(sectionId);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<SecretComponent>(
    section.components[0]
  );
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleSave() {
    const tokenName = name.trim();
    if (!tokenName) {
      onError("Token name is required.");
      return;
    }
    const required = COMPONENT_VALUE_FIELDS[selected];
    const fields: Record<string, string> = {};
    const missing: string[] = [];
    for (const field of required) {
      const value = (fieldValues[field.id] ?? "").trim();
      if (!value) {
        missing.push(field.label);
      } else {
        fields[field.id] = value;
      }
    }
    if (missing.length > 0) {
      onError(`Missing or empty field(s): ${missing.join(", ")}`);
      return;
    }
    setBusy(true);
    onError(null);
    try {
      const saved = await createSecret({
        component: selected,
        name: tokenName,
        fields,
      });
      setFieldValues({});
      await onSaved(saved);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to save secret.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="pp-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-secret-title"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="pp-modal">
        <div className="pp-modal-header">
          <h3 id="add-secret-title">Add secret</h3>
          <button
            type="button"
            className="pp-icon-btn"
            title="Close"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="pp-modal-body">
          <div className="pp-modal-field">
            <label className="pp-field-label" htmlFor="secret-section">
              Focus area
            </label>
            <select className="pp-field" id="secret-section" disabled>
              <option>{section.title}</option>
            </select>
          </div>
          <div className="pp-modal-field">
            <label className="pp-field-label" htmlFor="secret-component">
              Component
            </label>
            <select
              className="pp-field"
              id="secret-component"
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value as SecretComponent);
                setFieldValues({});
              }}
            >
              {section.components.map((key) => (
                <option key={key} value={key}>
                  {COMPONENT_LABELS[key]}
                </option>
              ))}
            </select>
          </div>
          <div className="pp-modal-field">
            <label className="pp-field-label" htmlFor="secret-name">
              Token name
            </label>
            <input
              className="pp-field"
              id="secret-name"
              type="text"
              placeholder="e.g. project1-ado-pat"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          {COMPONENT_VALUE_FIELDS[selected].map((field) => (
            <div className="pp-modal-field" key={field.id}>
              <label
                className="pp-field-label"
                htmlFor={`secret-value-${field.id}`}
              >
                {field.label}
              </label>
              <input
                className="pp-field"
                id={`secret-value-${field.id}`}
                type="password"
                placeholder={field.placeholder}
                value={fieldValues[field.id] ?? ""}
                autoComplete="off"
                onChange={(event) =>
                  setFieldValues((current) => ({
                    ...current,
                    [field.id]: event.target.value,
                  }))
                }
              />
            </div>
          ))}
          <p className="pp-modal-note">
            The value is sent once and stored encrypted. It will not appear
            again in this list.
          </p>
        </div>
        <div className="pp-modal-footer">
          <button
            type="button"
            className="pp-btn pp-btn-secondary"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            className="pp-btn pp-btn-primary"
            disabled={busy}
            onClick={() => void handleSave()}
          >
            Save secret
          </button>
        </div>
      </div>
    </div>
  );
}
