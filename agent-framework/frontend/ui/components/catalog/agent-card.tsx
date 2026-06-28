"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { StatusBadge } from "./status-badge";
import type { Agent } from "@/lib/types";

interface AgentCardProps {
  agent: Agent;
}

export function AgentCard({ agent }: AgentCardProps) {
  const [skillsExpanded, setSkillsExpanded] = useState(false);
  const isDimmed = agent.status === "in_development";

  return (
    <Card className={isDimmed ? "opacity-60" : ""}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1">
            <CardTitle className="text-base">{agent.display_name}</CardTitle>
            <CardDescription>{agent.description}</CardDescription>
          </div>
          <StatusBadge status={agent.status} />
        </div>
        <p className="text-xs text-muted-foreground mt-1">v{agent.version}</p>
      </CardHeader>
      {agent.skills.length > 0 && (
        <CardContent className="pt-0">
          <button
            onClick={() => setSkillsExpanded(!skillsExpanded)}
            className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            {skillsExpanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            {agent.skills.length} skill{agent.skills.length !== 1 ? "s" : ""}
          </button>
          {skillsExpanded && (
            <ul className="mt-2 space-y-1.5 pl-5">
              {agent.skills.map((skill, idx) => {
                const name = typeof skill === "string" ? skill : skill.name;
                const desc = typeof skill === "string" ? undefined : skill.description;
                return (
                  <li key={name ?? idx} className="text-xs">
                    <span className="font-mono text-foreground">{name}</span>
                    {desc && (
                      <span className="text-muted-foreground ml-1.5">
                        — {desc}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      )}
      {agent.skills.length === 0 && (
        <CardContent className="pt-0">
          <p className="text-xs text-muted-foreground italic">No skills registered</p>
        </CardContent>
      )}
    </Card>
  );
}
