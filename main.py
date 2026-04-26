import os
import json
import logging
from typing import List
from pydantic import BaseModel, Field
import google.generativeai as genai
from duckduckgo_search import DDGS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("IncidentResponse")

# --- SCHEMAS ---

class LogAnalysisHandoff(BaseModel):
    root_cause_diagnosis: str = Field(description="The most likely issue or bug")
    extracted_evidence: List[str] = Field(description="The strongest log evidence supporting the conclusion")
    uncertainty_or_alternatives: str = Field(description="Highlight uncertainty, missing evidence, or alternate hypotheses")
    confidence_level: str = Field(description="High, Medium, or Low")
    recommended_search_queries: List[str] = Field(description="1-2 highly specific search queries to find a fix for this root cause")

class SolutionOption(BaseModel):
    solution_summary: str = Field(description="Summary of the possible fix")
    pros: List[str] = Field(description="Pros of this solution")
    cons: List[str] = Field(description="Cons or drawbacks of this solution")
    risks: List[str] = Field(description="Risks, flagging actions that should not be attempted first in production")
    source_url: str = Field(description="The source URL backing this solution")

class ResearchHandoff(BaseModel):
    possible_solutions: List[SolutionOption] = Field(description="List of compared solutions")

class ResolutionPlan(BaseModel):
    best_recommended_solution: str = Field(description="The safest and most practical solution")
    pre_checks: List[str] = Field(description="Validation steps before the fix")
    remediation_steps: List[str] = Field(description="Ordered remediation steps (step-by-step operator instructions)")
    post_fix_validation: List[str] = Field(description="Post-fix validation checks")
    rollback_plan: str = Field(description="Rollback or safety notes if the fix fails")

# --- UTILS ---

def read_file(filepath: str) -> str:
    try:
        with open(filepath, 'r') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return ""

def get_gemini_model():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set. Please set it before running.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel('gemini-2.5-flash')

# --- AGENTS ---

def extract_log_metrics(nginx_access: str, nginx_error: str, app_error: str) -> dict:
    """Extract quantified metrics from logs for incident assessment"""
    
    error_count = app_error.count("ERROR")
    warning_count = app_error.count("WARN")
    timeout_count = nginx_error.count("Connection timed out")
    http_502 = nginx_access.count(" 502 ")
    http_504 = nginx_access.count(" 504 ")
    
    return {
        "total_errors": error_count,
        "total_warnings": warning_count,
        "timeout_errors": timeout_count,
        "http_502_responses": http_502,
        "http_504_responses": http_504,
        "incident_severity": "CRITICAL" if (http_502 + http_504) > 5 else "HIGH",
        "affected_services": ["api-service"]
    }

def agent_1_log_analysis() -> LogAnalysisHandoff:
    logger.info("Starting Agent 1: Log Analysis")
    
    nginx_access = read_file("nginx-access.log")
    nginx_error = read_file("nginx-error.log")
    app_error = read_file("app-error.log")
    
    prompt = f"""
    You are an expert Site Reliability Engineer analyzing a production incident.

    LOGS TO ANALYZE:
    --- nginx-access.log ---
    {nginx_access}
    
    --- nginx-error.log ---
    {nginx_error}
    
    --- app-error.log ---
    {app_error}
    
    YOUR ANALYSIS MUST INCLUDE:
    
    1. TIMELINE - When did incident start, escalate, become widespread?
    2. PATTERNS - Do errors escalate linearly or exponentially? Which endpoints are affected?
    3. ROOT CAUSE - Most likely cause with evidence and confidence level (High/Medium/Low)
    4. ALTERNATIVES - Other hypotheses if evidence is ambiguous
    
    Provide structured JSON.
    """
    
    model = get_gemini_model()
    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=LogAnalysisHandoff,
            temperature=0.2
        )
    )
    
    handoff = LogAnalysisHandoff.model_validate_json(response.text)
    logger.info(f"Agent 1 Diagnosis: {handoff.root_cause_diagnosis}")
    return handoff

def agent_2_solution_research(analysis: LogAnalysisHandoff) -> ResearchHandoff:
    logger.info("Starting Agent 2: Solution Research")
    
    # Use DDG to fetch real web results based on Agent 1's recommended search queries
    all_results = []
    queries = analysis.recommended_search_queries[:2]
    if not queries:
        queries = [analysis.root_cause_diagnosis]
        
    logger.info(f"Using search queries: {queries}")
    with DDGS() as ddgs:
        for q in queries:
            # Get top 3 text results per query
            try:
                results = list(ddgs.text(q, max_results=3))
                all_results.extend(results)
            except Exception as e:
                logger.error(f"Search failed for query '{q}': {e}")
    
    if not all_results:
        logger.warning("No web results found, using fallback with 4 solutions.")
        all_results = [
            {
                "title": "Rollback Recent Deployment",
                "body": "If a recent code deployment introduced the connection leak, rollback to the previous stable version. This is the fastest fix. Use Kubernetes rollout undo or standard deployment rollback procedure. Fast MTTR (5-10 minutes) and known safe.",
                "href": "https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-back-a-deployment"
            },
            {
                "title": "Implement PgBouncer Connection Pooler",
                "body": "PgBouncer is a connection pooler for PostgreSQL that multiplexes multiple client connections into fewer backend connections. Configuration uses pool_mode = transaction. Reduces backend connection load. Risk: adds network latency, requires infrastructure change.",
                "href": "https://www.pgbouncer.org/config.html"
            },
            {
                "title": "Increase SQLAlchemy Connection Pool Settings",
                "body": "Temporarily increase pool_size and max_overflow in SQLAlchemy QueuePool configuration. This does not fix the underlying leak - it just increases capacity. Use only as emergency mitigation while investigating. Monitor for memory exhaustion.",
                "href": "https://docs.sqlalchemy.org/en/20/core/pooling.html#api-sqlalchemy.pool.QueuePool"
            },
            {
                "title": "Increase PostgreSQL max_connections Parameter",
                "body": "Increase PostgreSQL's max_connections parameter in postgresql.conf. Default is 100, can be set much higher. However each connection consumes ~5MB RAM. Requires database restart, so this is a long-term fix, not suitable for incident response.",
                "href": "https://www.postgresql.org/docs/current/runtime-config-connection.html"
            }
        ]
        
    search_context = json.dumps(all_results, indent=2)
    logger.info(f"Retrieved {len(all_results)} search results.")
    
    prompt = f"""
    You are an expert Solutions Architect.
    Agent 1 identified this issue: {analysis.root_cause_diagnosis}
    
    We performed web scraping to find solutions. Here are the search results:
    {search_context}
    
    Your task:
    - Compare multiple possible solutions based ONLY on the provided search results.
    - Flag risky fixes, weak sources, and actions that should not be attempted first in production.
    - Formulate a structured recommendation handoff.
    """
    
    model = get_gemini_model()
    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=ResearchHandoff,
            temperature=0.2
        )
    )
    
    handoff = ResearchHandoff.model_validate_json(response.text)
    logger.info(f"Agent 2 found {len(handoff.possible_solutions)} potential solutions.")
    return handoff

def agent_3_resolution_planner(analysis: LogAnalysisHandoff, research: ResearchHandoff) -> ResolutionPlan:
    logger.info("Starting Agent 3: Resolution Planner")
    
    prompt = f"""
    You are an expert Incident Commander.
    
    Agent 1 Diagnosis:
    {analysis.model_dump_json(indent=2)}
    
    Agent 2 Researched Solutions:
    {research.model_dump_json(indent=2)}
    
    SELECT THE BEST SOLUTION AND EXPLAIN WHY:
    - Compare solutions: Speed vs Safety vs Permanence
    - SRE principle: Restore Service First, Investigate Later
    - Provide step-by-step commands (make it copy-pasteable)
    - Include pre-checks and post-validation
    - Add Go/No-Go decision points
    """
    
    model = get_gemini_model()
    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=ResolutionPlan,
            temperature=0.2
        )
    )
    
    plan = ResolutionPlan.model_validate_json(response.text)
    logger.info("Agent 3 Plan completed.")
    return plan

def main():
    try:
        # Extract metrics BEFORE analysis
        metrics = extract_log_metrics(
            read_file("nginx-access.log"),
            read_file("nginx-error.log"),
            read_file("app-error.log")
        )

        # Step 1
        analysis_handoff = agent_1_log_analysis()
        print("\n=== AGENT 1: LOG ANALYSIS ===")
        print(json.dumps(analysis_handoff.model_dump(), indent=2))
        
        # Step 2
        research_handoff = agent_2_solution_research(analysis_handoff)
        print("\n=== AGENT 2: SOLUTION RESEARCH ===")
        print(json.dumps(research_handoff.model_dump(), indent=2))
        
        # Step 3
        resolution_plan = agent_3_resolution_planner(analysis_handoff, research_handoff)
        print("\n=== AGENT 3: RESOLUTION PLAN ===")
        print(json.dumps(resolution_plan.model_dump(), indent=2))
        
        print("\n=== FINAL INCIDENT REPORT EXPORTED ===")
        with open("incident_report.json", "w") as f:
            full_report = {
                "log_metrics": metrics,
                "diagnosis": analysis_handoff.model_dump(),
                "research": research_handoff.model_dump(),
                "resolution_plan": resolution_plan.model_dump()
            }
            json.dump(full_report, f, indent=2)
            
    except Exception as e:
        logger.error(f"Incident Response Workflow failed: {e}")

if __name__ == "__main__":
    main()
