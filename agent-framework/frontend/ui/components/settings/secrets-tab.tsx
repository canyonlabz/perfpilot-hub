"use client";

import { useEffect, useState } from "react";
import { Info, Plus, X } from "lucide-react";
import {
  COMPONENT_LABELS,
  DEMO_SECRET_SECTIONS,
  type DemoComponent,
  type DemoSecret,
  type DemoSecretSection,
} from "@/lib/settings-demo";

type ModalState = {
  sectionId: string;
  sectionTitle: string;
  component: DemoComponent;
};

export function SecretsTab() {
  const [sections, setSections] = useState<DemoSecretSection[]>(
    DEMO_SECRET_SECTIONS
  );
  const [modal, setModal] = useState<ModalState | null>(null);

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
          Visual shell only — values are dummy masks and are not stored, logged,
          or sent anywhere.
        </span>
      </div>

      {sections.map((section) => (
        <div key={section.id} className="pp-card">
          <div className="pp-card-header">
            <div>
              <h3>{section.title}</h3>
              <p>{section.hint}</p>
            </div>
            <button
              type="button"
              className="pp-btn pp-btn-secondary"
              onClick={() =>
                setModal({
                  sectionId: section.id,
                  sectionTitle: section.title,
                  component: section.defaultComponent,
                })
              }
            >
              <Plus className="h-3 w-3" />
              Add secret
            </button>
          </div>
          <div className="pp-card-body pp-card-body-tight">
            {section.secrets.length === 0 ? (
              <div className="pp-section-empty">
                No secrets added yet. Click <strong>Add secret</strong> to create
                one.
              </div>
            ) : (
              section.secrets.map((secret) => (
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
                  <span />
                </div>
              ))
            )}
          </div>
        </div>
      ))}

      {modal && (
        <AddSecretModal
          sectionTitle={modal.sectionTitle}
          component={modal.component}
          onClose={() => setModal(null)}
          onSave={(entry) => {
            setSections((current) =>
              current.map((section) =>
                section.id === modal.sectionId
                  ? { ...section, secrets: [...section.secrets, entry] }
                  : section
              )
            );
            setModal(null);
          }}
        />
      )}
    </div>
  );
}

function AddSecretModal({
  sectionTitle,
  component,
  onClose,
  onSave,
}: {
  sectionTitle: string;
  component: DemoComponent;
  onClose: () => void;
  onSave: (secret: DemoSecret) => void;
}) {
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<DemoComponent>(component);
  const [value, setValue] = useState("");

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  function handleSave() {
    const tokenName = name.trim() || "untitled-secret";
    setValue("");
    onSave({
      id: `local-${Date.now()}`,
      name: tokenName,
      component: selected,
      masked: "••••••••••••demo",
      added: "Preview only",
    });
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
              <option>{sectionTitle}</option>
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
              onChange={(event) =>
                setSelected(event.target.value as DemoComponent)
              }
            >
              {Object.entries(COMPONENT_LABELS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
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
          <div className="pp-modal-field">
            <label className="pp-field-label" htmlFor="secret-value">
              Token value
            </label>
            <input
              className="pp-field"
              id="secret-value"
              type="password"
              placeholder="paste the token"
              value={value}
              autoComplete="off"
              onChange={(event) => setValue(event.target.value)}
            />
          </div>
          <p className="pp-modal-note">
            The typed value is discarded on save. Only a dummy masked name is
            shown in this preview.
          </p>
        </div>
        <div className="pp-modal-footer">
          <button type="button" className="pp-btn pp-btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="pp-btn pp-btn-primary" onClick={handleSave}>
            Save secret
          </button>
        </div>
      </div>
    </div>
  );
}
