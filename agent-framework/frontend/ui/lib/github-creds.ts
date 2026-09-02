"use client";

/**
 * Encrypted, session-scoped storage for GitHub credentials attached from the
 * PerfPilot Web UI.
 *
 * Mirrors the SharePoint MCP crypto pattern (AES-256-GCM, per-session
 * key material) but runs entirely in the browser using the Web Crypto API.
 *
 * Design contract:
 *   1. Credentials never touch localStorage. They live in sessionStorage and
 *      are wiped when the tab is closed.
 *   2. The AES key is derived from a per-session salt held only in
 *      sessionStorage. Reloading the tab is fine; opening a new tab or a
 *      new browser session re-derives a fresh key and any previously
 *      saved ciphertext becomes unreadable.
 *   3. The plaintext token is never returned unless the caller explicitly
 *      asks for it. The public read helper returns metadata only
 *      (url / branch / path / owner / repo / has_token / updated_at).
 *   4. Every helper is a no-op on the server (SSR) — callers can use them
 *      unconditionally in React components.
 */

const STORAGE_KEY = "perfpilot_github_creds_v1";
const SALT_KEY = "perfpilot_github_creds_salt_v1";
const SESSION_PASSPHRASE_KEY = "perfpilot_github_creds_pp_v1";
const AES_KEY_LENGTH_BITS = 256;
const PBKDF2_ITERATIONS = 150_000;
const IV_LENGTH_BYTES = 12;

function isBrowser(): boolean {
  return typeof window !== "undefined" && typeof crypto !== "undefined";
}

function toBase64(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (let i = 0; i < view.length; i += 1) {
    binary += String.fromCharCode(view[i]);
  }
  return btoa(binary);
}

function fromBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function ensurePassphrase(): string {
  const existing = sessionStorage.getItem(SESSION_PASSPHRASE_KEY);
  if (existing) return existing;
  const random = new Uint8Array(32);
  crypto.getRandomValues(random);
  const passphrase = toBase64(random);
  sessionStorage.setItem(SESSION_PASSPHRASE_KEY, passphrase);
  return passphrase;
}

function ensureSalt(): Uint8Array {
  const existing = sessionStorage.getItem(SALT_KEY);
  if (existing) return fromBase64(existing);
  const salt = new Uint8Array(16);
  crypto.getRandomValues(salt);
  sessionStorage.setItem(SALT_KEY, toBase64(salt));
  return salt;
}

async function deriveKey(): Promise<CryptoKey> {
  const passphrase = ensurePassphrase();
  const salt = ensureSalt();
  const encoder = new TextEncoder();
  const baseKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(passphrase),
    "PBKDF2",
    false,
    ["deriveKey"]
  );
  return crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt,
      iterations: PBKDF2_ITERATIONS,
      hash: "SHA-256",
    },
    baseKey,
    { name: "AES-GCM", length: AES_KEY_LENGTH_BITS },
    false,
    ["encrypt", "decrypt"]
  );
}

async function encryptString(plaintext: string): Promise<string> {
  const key = await deriveKey();
  const iv = new Uint8Array(IV_LENGTH_BYTES);
  crypto.getRandomValues(iv);
  const cipherBuffer = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    new TextEncoder().encode(plaintext)
  );
  return JSON.stringify({
    v: 1,
    iv: toBase64(iv),
    data: toBase64(cipherBuffer),
  });
}

async function decryptString(payload: string): Promise<string | null> {
  try {
    const parsed = JSON.parse(payload) as {
      v?: number;
      iv?: string;
      data?: string;
    };
    if (parsed.v !== 1 || !parsed.iv || !parsed.data) return null;
    const key = await deriveKey();
    const iv = fromBase64(parsed.iv);
    const cipher = fromBase64(parsed.data);
    const plainBuffer = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv },
      key,
      cipher
    );
    return new TextDecoder().decode(plainBuffer);
  } catch {
    return null;
  }
}

/**
 * Public metadata shape returned to React components. The token itself is
 * intentionally omitted — call `revealToken()` explicitly when a workflow
 * needs it.
 */
export interface GitHubCredsMetadata {
  url: string;
  branch: string;
  path: string;
  owner: string;
  repo: string;
  has_token: boolean;
  updated_at: string;
}

interface StoredCreds {
  url: string;
  token: string;
  branch: string;
  path: string;
  updated_at: string;
}

const GITHUB_URL_REGEX = /^https:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/i;

export interface ParsedGitHubUrl {
  owner: string;
  repo: string;
  normalized_url: string;
}

export function parseGitHubUrl(rawUrl: string): ParsedGitHubUrl | null {
  const trimmed = (rawUrl || "").trim();
  if (!trimmed) return null;
  const match = trimmed.match(GITHUB_URL_REGEX);
  if (!match) return null;
  const owner = match[1];
  const repo = match[2];
  return {
    owner,
    repo,
    normalized_url: `https://github.com/${owner}/${repo}`,
  };
}

async function readStored(): Promise<StoredCreds | null> {
  if (!isBrowser()) return null;
  const ciphertext = sessionStorage.getItem(STORAGE_KEY);
  if (!ciphertext) return null;
  const plaintext = await decryptString(ciphertext);
  if (!plaintext) return null;
  try {
    return JSON.parse(plaintext) as StoredCreds;
  } catch {
    return null;
  }
}

function toMetadata(stored: StoredCreds): GitHubCredsMetadata {
  const parsed = parseGitHubUrl(stored.url);
  return {
    url: stored.url,
    branch: stored.branch,
    path: stored.path,
    owner: parsed?.owner ?? "",
    repo: parsed?.repo ?? "",
    has_token: Boolean(stored.token),
    updated_at: stored.updated_at,
  };
}

export interface SaveGitHubCredsInput {
  url: string;
  token: string;
  branch?: string;
  path?: string;
}

export interface SaveResult {
  ok: boolean;
  error?: string;
  metadata?: GitHubCredsMetadata;
}

export async function saveGitHubCreds(
  input: SaveGitHubCredsInput
): Promise<SaveResult> {
  if (!isBrowser()) return { ok: false, error: "not-in-browser" };

  const parsed = parseGitHubUrl(input.url);
  if (!parsed) {
    return {
      ok: false,
      error:
        "Only https://github.com/<owner>/<repo> URLs are supported. SSH and enterprise hosts are not accepted from this UI.",
    };
  }
  if (!input.token || input.token.trim().length < 8) {
    return {
      ok: false,
      error: "Personal access token looks invalid (too short).",
    };
  }

  const stored: StoredCreds = {
    url: parsed.normalized_url,
    token: input.token.trim(),
    branch: (input.branch ?? "").trim(),
    path: (input.path ?? "").trim(),
    updated_at: new Date().toISOString(),
  };

  const ciphertext = await encryptString(JSON.stringify(stored));
  sessionStorage.setItem(STORAGE_KEY, ciphertext);
  window.dispatchEvent(new Event("perfpilot:github-creds-changed"));

  return { ok: true, metadata: toMetadata(stored) };
}

export async function getGitHubCredsMetadata(): Promise<GitHubCredsMetadata | null> {
  const stored = await readStored();
  return stored ? toMetadata(stored) : null;
}

/**
 * Returns the plaintext personal access token for the current session.
 *
 * Intentionally separate from `getGitHubCredsMetadata()` so React components
 * default to metadata-only reads. Only workflows that must actually push to
 * Git should call this helper.
 */
export async function revealGitHubToken(): Promise<string | null> {
  const stored = await readStored();
  return stored?.token ?? null;
}

export function clearGitHubCreds(): void {
  if (!isBrowser()) return;
  sessionStorage.removeItem(STORAGE_KEY);
  sessionStorage.removeItem(SESSION_PASSPHRASE_KEY);
  sessionStorage.removeItem(SALT_KEY);
  window.dispatchEvent(new Event("perfpilot:github-creds-changed"));
}

/**
 * Subscribe to credential-change events so components can react when the
 * user saves or clears creds elsewhere in the app.
 */
export function subscribeToCredsChanges(listener: () => void): () => void {
  if (!isBrowser()) return () => {};
  window.addEventListener("perfpilot:github-creds-changed", listener);
  return () => {
    window.removeEventListener("perfpilot:github-creds-changed", listener);
  };
}
