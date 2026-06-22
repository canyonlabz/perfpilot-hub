import { NextRequest } from "next/server";
import {
  CopilotRuntime,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";

const COOKIE_NAME = "perfpilot_token";

function extractToken(raw: string): string | undefined {
  const match = raw.match(new RegExp(`(?:^|;\\s*)${COOKIE_NAME}=([^;]+)`));
  return match?.[1];
}

export const POST = async (req: NextRequest) => {
  const cookie = req.headers.get("cookie") || "";
  const token = extractToken(cookie);

  const headers: Record<string, string> = { cookie };
  if (token) {
    headers["X-PerfPilot-Token"] = token;
  }

  const runtime = new CopilotRuntime({
    agents: {
      "perfpilot-orchestrator": new HttpAgent({
        url: "http://localhost:8002/copilotkit/",
        headers,
      }),
    },
  });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    endpoint: "/api/copilotkit",
  });
  return handleRequest(req);
};
