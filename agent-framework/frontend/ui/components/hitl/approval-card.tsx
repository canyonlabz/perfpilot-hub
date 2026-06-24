"use client";

import { useState } from "react";
import {
  ShieldCheck,
  ShieldX,
  AlertTriangle,
  Loader2,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { approveHitl, rejectHitl } from "@/lib/api";
import type { HitlApproval } from "@/lib/types";

interface ApprovalCardProps {
  approval: HitlApproval;
  onDecided: (updated: HitlApproval) => void;
}

export function ApprovalCard({ approval, onDecided }: ApprovalCardProps) {
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(
    null
  );
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showArtifact, setShowArtifact] = useState(false);

  const { prompt } = approval;

  const handleApprove = async () => {
    setSubmitting("approve");
    setError(null);
    try {
      const updated = await approveHitl(approval.id);
      onDecided(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approve failed");
      setSubmitting(null);
    }
  };

  const handleReject = async () => {
    if (!showFeedback) {
      setShowFeedback(true);
      return;
    }
    setSubmitting("reject");
    setError(null);
    try {
      const updated = await rejectHitl(approval.id, feedback || undefined);
      onDecided(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reject failed");
      setSubmitting(null);
    }
  };

  const hasArtifact =
    prompt.artifact && Object.keys(prompt.artifact).length > 0;

  return (
    <div className="rounded-lg border-2 border-amber-300 dark:border-amber-700 bg-amber-50/50 dark:bg-amber-950/30 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-amber-100/60 dark:bg-amber-900/30 border-b border-amber-200 dark:border-amber-800">
        <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0" />
        <span className="text-sm font-semibold text-amber-800 dark:text-amber-200">
          Approval Required
        </span>
      </div>

      {/* Body */}
      <div className="px-3 py-2.5 space-y-2">
        {/* Title */}
        <p className="text-sm font-medium text-foreground">
          {prompt.title || "Action requires your approval"}
        </p>

        {/* Summary */}
        {prompt.summary && (
          <p className="text-xs text-muted-foreground leading-relaxed">
            {prompt.summary}
          </p>
        )}

        {/* Artifact details (collapsible) */}
        {hasArtifact && (
          <div>
            <button
              onClick={() => setShowArtifact((prev) => !prev)}
              className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              {showArtifact ? (
                <ChevronUp className="h-3 w-3" />
              ) : (
                <ChevronDown className="h-3 w-3" />
              )}
              Details
            </button>
            {showArtifact && (
              <div className="mt-1 px-2 py-1.5 rounded bg-muted/60 text-[11px] font-mono space-y-0.5">
                {Object.entries(prompt.artifact!).map(([key, value]) => (
                  <div key={key} className="flex gap-2">
                    <span className="text-muted-foreground shrink-0">
                      {key}:
                    </span>
                    <span className="text-foreground break-all">
                      {String(value)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Feedback textarea (shown when reject is clicked) */}
        {showFeedback && (
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="Reason for rejection (optional)..."
            rows={2}
            className="w-full px-2 py-1.5 rounded border border-input bg-background text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none"
            disabled={submitting !== null}
          />
        )}

        {/* Error message */}
        {error && (
          <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
        )}

        {/* Action buttons */}
        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={handleApprove}
            disabled={submitting !== null}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting === "approve" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <ShieldCheck className="h-3 w-3" />
            )}
            Approve
          </button>
          <button
            onClick={handleReject}
            disabled={submitting !== null}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting === "reject" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <ShieldX className="h-3 w-3" />
            )}
            Reject
          </button>
          {showFeedback && !submitting && (
            <button
              onClick={() => {
                setShowFeedback(false);
                setFeedback("");
              }}
              className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
