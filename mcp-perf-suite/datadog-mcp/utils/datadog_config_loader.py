# datadog_mcp/utils/datadog_config_loader.py

import json
import os
from typing import Dict, Any, Optional
from fastmcp import Context    # ✅ FastMCP 3.x import
from utils.config import load_config

config = load_config()
datadog_cfg = config.get("datadog", {})
environments_json_path = datadog_cfg.get("environments_json_path")
queries_json_path = datadog_cfg.get("custom_queries_json_path")

# -----------------------------------------------
# Environment loader
# -----------------------------------------------

async def load_environment_json(env_name: str, ctx: Context) -> Dict[str, Any]:
    """
    Loads the complete environment configuration for a given environment from environments.json and stores it in the context.
    Args:
        env_name (str): The environment name ("QA", "UAT", etc.)
        ctx: FastMCP context (for info/error reporting)
    Returns:
        dict: Complete environment configuration including env_tag, metadata, tags, services, hosts, and kubernetes sections.
    """
    with open(environments_json_path, "r", encoding="utf-8") as f:
        envdata = json.load(f)
    
    env_config = envdata["environments"].get(env_name)
    if not env_config:
        await ctx.error(f"Environment '{env_name}' not found in environments.json")
        raise ValueError(f"Environment '{env_name}' not found in environments.json")
    
    # Add the environment name to the config for reference
    env_config["environment_name"] = env_name
    
    # Store in context for later steps
    await ctx.set_state("env_config", json.dumps(env_config))
    await ctx.set_state("env_name", env_name)

    # Extract key info for the log message
    env_tag = env_config.get("env_tag", "unknown")
    host_count = len(env_config.get("hosts", []))
    k8s_services = len(env_config.get("kubernetes", {}).get("services", []))
    await ctx.info(f"Environment '{env_name}' loaded with env_tag: {env_tag}, {host_count} hosts, {k8s_services} k8s services")

    return env_config

# -----------------------------------------------
# Custom query loader
# -----------------------------------------------

async def load_custom_queries_json() -> dict:
    """
    Load custom Datadog APM, Log, and KPI queries from custom_queries.json.

    The path is defined in config["datadog"]["custom_queries_json_path"].
    Returns a dict with at least:
        { "apm_queries": {}, "log_queries": {}, "kpi_queries": {} }.
    """
    if not queries_json_path:
        return {
            "schema_version": "2.0",
            "apm_queries": {},
            "log_queries": {},
            "kpi_queries": {}
        }

    if not os.path.exists(queries_json_path):
        return {
            "schema_version": "2.0",
            "apm_queries": {},
            "log_queries": {},
            "kpi_queries": {}
        }

    with open(queries_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Normalize expected keys
    data.setdefault("apm_queries", {})
    data.setdefault("log_queries", {})
    data.setdefault("kpi_queries", {})

    return data
