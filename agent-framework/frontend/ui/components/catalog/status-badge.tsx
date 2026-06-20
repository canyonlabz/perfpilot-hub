import { Badge } from "@/components/ui/badge";
import { CheckCircle2, Clock } from "lucide-react";

interface StatusBadgeProps {
  status: "available" | "in_development";
}

export function StatusBadge({ status }: StatusBadgeProps) {
  if (status === "available") {
    return (
      <Badge variant="success" className="flex items-center gap-1 text-xs">
        <CheckCircle2 className="h-3 w-3" />
        Available
      </Badge>
    );
  }

  return (
    <Badge variant="warning" className="flex items-center gap-1 text-xs">
      <Clock className="h-3 w-3" />
      In Development
    </Badge>
  );
}
