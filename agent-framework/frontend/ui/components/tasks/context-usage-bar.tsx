"use client";

import { formatTokenCount, getContextTier } from "@/lib/utils";

interface ContextUsageBarProps {
  tokens: number;
  utilizationPct: number;
  limit: number;
}

const TIER_COLORS = {
  green: {
    bar: "bg-green-500 dark:bg-green-400",
    text: "text-green-700 dark:text-green-300",
  },
  amber: {
    bar: "bg-amber-500 dark:bg-amber-400",
    text: "text-amber-700 dark:text-amber-300",
  },
  red: {
    bar: "bg-red-500 dark:bg-red-400",
    text: "text-red-700 dark:text-red-300",
  },
} as const;

export function ContextUsageBar({ tokens, utilizationPct, limit }: ContextUsageBarProps) {
  const tier = getContextTier(utilizationPct);
  const colors = TIER_COLORS[tier];
  const widthPct = Math.min(utilizationPct, 100);

  const tooltip = [
    `${tokens.toLocaleString()} tokens`,
    `${limit.toLocaleString()} token limit`,
    utilizationPct >= 80 ? "Compaction territory (≥80%)" : "",
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <div className="flex items-center gap-2" title={tooltip}>
      <span className={`text-[10px] font-medium whitespace-nowrap ${colors.text}`}>
        Task Context: {utilizationPct.toFixed(1)}%
      </span>

      <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${colors.bar}`}
          style={{ width: `${widthPct}%` }}
        />
      </div>

      <span className="text-[10px] text-muted-foreground whitespace-nowrap">
        {formatTokenCount(tokens)}/{formatTokenCount(limit)}
      </span>
    </div>
  );
}
