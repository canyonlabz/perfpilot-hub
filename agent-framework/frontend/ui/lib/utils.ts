import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// --- Context token helpers ---

export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return tokens.toString();
}

export type ContextTier = "green" | "amber" | "red";

export function getContextTier(utilizationPct: number): ContextTier {
  if (utilizationPct >= 80) return "red";
  if (utilizationPct >= 50) return "amber";
  return "green";
}
