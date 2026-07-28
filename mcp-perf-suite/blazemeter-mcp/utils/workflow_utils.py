# DEPRECATED: This module is unused and will be removed.
# It will be replaced by a future AI Agent framework (LangChain + LangGraph + A2A).
# blazemeter-mcp/utils/workflow_utils.py
import re
import asyncio

from fastmcp import Context  # Ensure Context is properly imported

# ===============================================
# Helper Functions
# ===============================================

async def prerequisites_satisfied(ctx: Context, step: dict) -> bool:
    """
    Check prerequisites for an optional step (e.g., required context keys).
    Returns True if all required inputs are present, else False.
    """
    prerequisites = step.get("prerequisites")
    if not prerequisites:
        return True
    for key in prerequisites:
        value = await ctx.get_state(key)
        if value is None:
            return False
    return True

async def interpolate_params(params: dict, ctx: Context, config: dict) -> dict:
    """
    Resolves all ${variable} placeholders in params using context or config.
    Returns a dictionary with all interpolated values.
    """
    async def interpolate_value(val):
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            var_name = val[2:-1]
            # Priority: context, then config
            ctx_val = await ctx.get_state(var_name)
            return ctx_val if ctx_val is not None else config.get(var_name)
        return val

    result = {}
    for k, v in params.items():
        result[k] = await interpolate_value(v)
    return result

async def dispatch_tool(tool_name: str, params: dict, ctx: Context, tool_registry: dict):
    """
    Dynamically finds and executes the tool by name using MCP registration.
    Calls tool with given params and context. Handles both async and sync tools.
    """
    tool_fn = tool_registry.get(tool_name)
    if not tool_fn:
        raise ValueError(f"Tool '{tool_name}' not found in registry")
    
    # Simple approach - just call the function directly for now
    try:
        if asyncio.iscoroutinefunction(tool_fn):
            return await tool_fn(**params, ctx=ctx)
        else:
            return tool_fn(**params, ctx=ctx)
    except Exception as e:
        raise Exception(f"Error calling tool {tool_name}: {str(e)}")

async def poll_until_complete(step: dict, params: dict, ctx: Context, config: dict, tool_registry: dict):
    """
    Performs polling for repeat_until steps, using config-driven intervals, max_retries, timeouts.
    Returns when completion condition is met or max retries is reached.
    """
    repeat_until = step.get("repeat_until")
    if not repeat_until:
        return

    # Polling settings
    interval = int(repeat_until.get("polling_interval_seconds", config.get("polling_interval_seconds", 30)))
    max_retries = int(repeat_until.get("max_retries", config.get("polling_max_retries", 3)))
    timeout_seconds = int(repeat_until.get("timeout_seconds", config.get("polling_timeout_seconds", 600)))

    condition = repeat_until.get("equals", {})
    elapsed = 0
    retries = 0

    while retries < max_retries and elapsed < timeout_seconds:
        result = await dispatch_tool(step['tool'], params, ctx, tool_registry)
        # Assumes result is dict-like
        satisfied = all(result.get(k) == v for k, v in condition.items())
        if satisfied:
            await ctx.info(f"Step '{step['tool']}' completed polling successfully.")
            return result
        await ctx.info(f"Polling '{step['tool']}' did not complete. Waiting {interval}s before retry...")
        await asyncio.sleep(interval)
        retries += 1
        elapsed += interval
    await ctx.error(f"Polling for '{step['tool']}' exceeded maximum retries or timeout.")
    raise TimeoutError(f"Polling for '{step['tool']}' failed.")
