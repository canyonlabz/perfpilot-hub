# utils/ai_analyst.py
import httpx
import json
import os
import pandas as pd
from typing import Dict, Any
from fastmcp import Context

# -----------------------------------------------
# AI analysis functions
# -----------------------------------------------
async def generate_ai_insights(combined_data: Dict[str, Any], test_run_id: str) -> Dict[str, Any]:
    """Generate AI-powered insights using OpenAI"""
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OpenAI API key not found"}
    
    # Prepare context for AI analysis
    context = prepare_analysis_context(combined_data)
    
    prompt = f"""
    As a performance testing expert, analyze this performance test data and provide insights:
    
    Test Run ID: {test_run_id}
    
    Analysis Data:
    {json.dumps(context, indent=2)}
    
    Please provide:
    1. Key performance findings
    2. Infrastructure bottlenecks identified
    3. Recommended optimizations
    4. Risk assessment
    5. Next steps for improvement
    
    Keep the analysis concise and actionable for technical teams.
    """
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2000,
                    "temperature": 0.3
                },
                timeout=30.0
            )
            response.raise_for_status()
            
            result = response.json()
            return {
                "ai_analysis": result["choices"][0]["message"]["content"],
                "model_used": "gpt-4o-mini",
                "generated_at": pd.Timestamp.now().isoformat()
            }
            
    except Exception as e:
        return {"error": f"AI analysis failed: {str(e)}"}

def prepare_analysis_context(combined_data: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare simplified context for AI analysis"""
    
    context = {}
    
    # Extract key performance metrics
    if 'performance_analysis' in combined_data:
        perf = combined_data['performance_analysis'].get('overall_stats', {})
        context['performance'] = {
            "avg_response_time": perf.get('avg_response_time'),
            "success_rate": perf.get('success_rate'),
            "error_count": perf.get('error_count'),
            "p95_response_time": perf.get('p95_response_time')
        }
    
    # Extract infrastructure summary
    if 'infrastructure_analysis' in combined_data:
        context['infrastructure'] = combined_data['infrastructure_analysis'].get('summary', {})
    
    # Extract anomalies summary
    if 'anomaly_detection' in combined_data:
        context['anomalies'] = combined_data['anomaly_detection'].get('summary', {})
    
    return context

def summarize_host_metrics(host_data: pd.DataFrame) -> Dict:
    """Summarize host metrics data"""
    # Implementation for host metrics summarization
    return {}

def summarize_k8s_metrics(k8s_data: pd.DataFrame) -> Dict:
    """Summarize K8s metrics data"""
    # Implementation for K8s metrics summarization
    return {}
