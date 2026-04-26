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

def agent_1_log_analysis() -> LogAnalysisHandoff:
    logger.info("Starting Agent 1: Log Analysis")
    
    nginx_access = read_file("nginx-access.log")
    nginx_error = read_file("nginx-error.log")
    app_error = read_file("app-error.log")
    
    prompt = f"""
    You are an expert Site Reliability Engineer.
    Analyze the following logs from a production API incident:
    
    --- nginx-access.log ---
    {nginx_access}
    
    --- nginx-error.log ---
    {nginx_error}
    
    --- app-error.log ---
    {app_error}
    
    Your task:
    - Identify the most likely issue or bug.
    - Extract the strongest log evidence supporting that conclusion.
    - Highlight uncertainty, missing evidence, or alternate hypotheses.
    - Provide a structured handoff.
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
        logger.warning("No web results found, using fallback simulated results.")
        all_results = [
            {"title": "Postgres Connection Pool Exhaustion", 
             "body": "Increase max_connections in postgresql.conf or scale up the connection pooler like pgbouncer. Be careful not to set max_connections too high or you will run out of memory.",
             "href": "https://wiki.postgresql.org/wiki/Tuning_Your_PostgreSQL_Server"}
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
    
    Your task:
    - Review the findings and solution options.
    - Select the safest and most practical solution for a production environment.
    - Convert that solution into clear step-by-step operator instructions.
    - Include validation steps before, during, and after the fix.
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
                "diagnosis": analysis_handoff.model_dump(),
                "research": research_handoff.model_dump(),
                "resolution_plan": resolution_plan.model_dump()
            }
            json.dump(full_report, f, indent=2)
            
    except Exception as e:
        logger.error(f"Incident Response Workflow failed: {e}")

if __name__ == "__main__":
    main()
