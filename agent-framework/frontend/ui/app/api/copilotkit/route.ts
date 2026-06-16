import { NextRequest } from "next/server";
import {
  CopilotRuntime,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";

export const POST = async (req: NextRequest) => {
  const cookie = req.headers.get("cookie") || "";

  const runtime = new CopilotRuntime({
    agents: {
      "perfpilot-orchestrator": new HttpAgent({
        url: "http://localhost:8002/copilotkit/",
        headers: {
          cookie,
        },
      }),
    },
  });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    endpoint: "/api/copilotkit",
  });
  return handleRequest(req);
};
