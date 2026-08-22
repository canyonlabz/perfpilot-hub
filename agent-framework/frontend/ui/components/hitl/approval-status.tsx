"use client";

import { ShieldCheck, ShieldX } from "lucide-react";
import type { HitlApproval } from "@/lib/types";

interface ApprovalStatusProps {
  approval: HitlApproval;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}

export function ApprovalStatus({ approval }: ApprovalStatusProps) {
  const isApproved = approval.decision === "approved";

  return (
    <div
      className={`rounded-lg border px-3 py-2 text-xs ${
        isApproved
          ? "border-green-200 dark:border-green-800 bg-green-50/50 dark:bg-green-950/30"
          : "border-red-200 dark:border-red-800 bg-red-50/50 dark:bg-red-950/30"
      }`}
    >
      <div className="flex items-center gap-2">
        {isApproved ? (
          <ShieldCheck className="h-3.5 w-3.5 text-green-600 dark:text-green-400 shrink-0" />
        ) : (
          <ShieldX className="h-3.5 w-3.5 text-red-600 dark:text-red-400 shrink-0" />
        )}
        <span
          className={`font-medium ${
            isApproved
              ? "text-green-700 dark:text-green-300"
              : "text-red-700 dark:text-red-300"
          }`}
        >
          {isApproved ? "Approved" : "Rejected"}
        </span>
        <span className="text-muted-foreground ml-auto">
          {approval.prompt.title || "Approval decision"}
        </span>
      </div>

      {/* Rejection feedback */}
      {!isApproved && approval.feedback && (
        <p className="mt-1 pl-5.5 text-muted-foreground italic">
          &ldquo;{approval.feedback}&rdquo;
        </p>
      )}

      {/* Metadata line */}
      <div className="mt-1 pl-5.5 flex items-center gap-2 text-[10px] text-muted-foreground">
        {approval.decided_by && <span>by {approval.decided_by}</span>}
        {approval.decided_at && <span>{formatTime(approval.decided_at)}</span>}
      </div>
    </div>
  );
}
