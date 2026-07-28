"""
services/comparison_chart_generator.py
Helper functions for comparison chart generation.

This module contains helper functions for loading and extracting data
from multiple test runs for comparison chart generation.
"""

import json
from pathlib import Path
from typing import Dict, Optional, List

# Import config at module level
from utils.config import load_config

# Load configuration
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', './artifacts'))


# -----------------------------------------------
# Helper Functions for Comparison Charts
# -----------------------------------------------

def _load_run_metadata(run_id: str) -> Optional[Dict]:
    """
    Load report_metadata_{run_id}.json for a single run.
    
    Args:
        run_id: Test run identifier
        
    Returns:
        Dict containing report metadata, or None if file not found
    """
    metadata_path = ARTIFACTS_PATH / run_id / "reports" / f"report_metadata_{run_id}.json"
    
    if not metadata_path.exists():
        return None
    
    try:
        with open(metadata_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading metadata for run {run_id}: {str(e)}")
        return None


def _extract_entity_metrics(
    run_metadata_list: List[Dict], 
    entity_name: str, 
    metric_type: str
) -> List[Dict]:
    """
    Extract metrics for a specific entity across all runs.
    
    Args:
        run_metadata_list: List of loaded metadata dicts
        entity_name: Name of the host/service (e.g., "Perf::authentication2-svc*")
        metric_type: 'cpu' or 'memory'
        
    Returns:
        List of dicts with run_id and metric values:
        - For CPU: {"run_id": str, "peak_cores": float, "avg_cores": float}
        - For Memory: {"run_id": str, "peak_gb": float, "avg_gb": float}
    """
    run_data = []
    
    for meta in run_metadata_list:
        run_id = meta.get("run_id")
        entities = meta.get("infrastructure_metrics", {}).get("entities", [])
        
        # Find the entity by name
        entity = next(
            (e for e in entities if e.get("entity_name") == entity_name), 
            None
        )
        
        if entity:
            if metric_type == "cpu":
                run_data.append({
                    "run_id": run_id,
                    "peak_cores": entity.get("cpu_peak_cores", 0),
                    "avg_cores": entity.get("cpu_avg_cores", 0)
                })
            elif metric_type == "memory":
                run_data.append({
                    "run_id": run_id,
                    "peak_gb": entity.get("memory_peak_gb", 0),
                    "avg_gb": entity.get("memory_avg_gb", 0)
                })
    
    return run_data


def _get_unique_entities(run_metadata_list: List[Dict]) -> List[str]:
    """
    Get unique entity names across all runs.
    
    Args:
        run_metadata_list: List of loaded metadata dicts
        
    Returns:
        Sorted list of unique entity names
    """
    all_entities = set()
    
    for meta in run_metadata_list:
        entities = meta.get("infrastructure_metrics", {}).get("entities", [])
        for entity in entities:
            entity_name = entity.get("entity_name")
            if entity_name:
                all_entities.add(entity_name)
    
    return sorted(list(all_entities))


def _load_all_run_metadata(run_id_list: List[str]) -> tuple[List[Dict], List[str]]:
    """
    Load metadata for all runs in the list.
    
    Args:
        run_id_list: List of test run IDs
        
    Returns:
        Tuple of (loaded_metadata_list, errors_list)
    """
    run_metadata_list = []
    errors = []
    
    for run_id in run_id_list:
        metadata = _load_run_metadata(run_id)
        if metadata:
            run_metadata_list.append(metadata)
        else:
            errors.append(f"Metadata not found for run {run_id}")
    
    return run_metadata_list, errors
