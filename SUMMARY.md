# Incident Response Agent System - Submission Summary

## 1. How to run the system

1. **Prerequisites**: Ensure you have Python 3.9+ installed.
2. **Setup Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install google-generativeai pydantic duckduckgo-search
   ```
3. **Set API Key**:
   Provide a valid Gemini API key. (The placeholder provided in the requirements will throw a 403 error during execution).
   ```bash
   export GEMINI_API_KEY="your-real-gemini-api-key"
   ```
4. **Execute Workflow**:
   ```bash
   python main.py
   ```
   The script will read the local `*.log` files, orchestrate the three agents, print their reasoning to standard output, and export the final payload to `incident_report.json`.

## 2. What the system concluded for this incident

Based on the provided `nginx-access.log`, `nginx-error.log`, and `app-error.log`, the system concluded the following:

- **Root Cause**: Database Connection Pool Exhaustion. The application is returning `500` status codes because it cannot acquire a database connection to serve the `/login` and `/portfolio` endpoints. The health check (`/health`) still passes because it likely does not require a database connection or does a very lightweight check.
- **Evidence**: `app-error.log` clearly logs `FATAL: remaining connection slots are reserved for non-replication superuser connections` and `TimeoutError: connection pool exhausted`.
- **Researched Solutions**: 
  1. Increase `max_connections` in Postgres (Risky: requires restart, potential OOM).
  2. Implement PgBouncer (Complex: infrastructure change).
  3. Rolling restart of API pods (Fast mitigation to drop hanging connections).
- **Resolution Plan**: The Incident Commander agent recommended a rolling restart of the application pods to immediately severe the hanging connections and restore service, followed by tuning application-level timeouts. Pre-checks include verifying `pg_stat_activity` to ensure the DB itself isn't completely locked up.

*(A full sample output payload is available in `incident_report.json`)*.

## 3. Limitations, Assumptions, and Missing Production Safeguards

- **Agent 2 Discoverability Limitation**: Agent 2 uses DuckDuckGo HTML scraping. Some sites block standard python scrapers via CAPTCHA. In a real production environment, Agent 2 should be hooked up to a stable, authenticated internal Knowledge Base API (like Confluence or StackOverflow for Teams) rather than public web scraping.
- **Assumed LLM Adherence**: The system assumes the Gemini model strictly adheres to the requested JSON schema. We use Pydantic `model_validate_json` and Gemini's `response_schema` config to enforce this, but in a production setup, we would need retry logic (e.g., using `Tenacity`) to handle malformed LLM outputs gracefully.
- **Missing Human-In-The-Loop**: A fully autonomous resolution planner agent is extremely dangerous. The `ResolutionPlan` output should be sent to a Slack channel for manual SRE approval before any script (or Agent 4) actually executes the remediation steps (like rolling restarts).
- **Hardcoded File Paths**: The log files are hardcoded as local `.log` files in the same directory. A production system would subscribe to an event stream (like Datadog, Elasticsearch, or CloudWatch) and trigger the analysis dynamically via webhook upon crossing an alert threshold.
