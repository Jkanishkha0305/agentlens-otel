# AgentLens — Build Spec for Codex

## Context
AgentLens is a multi-agent GitHub PR triage system that extends Prove AI's
observability-pipeline (https://github.com/prove-ai/observability-pipeline).

Prove AI's current OTEL collector config has only 2 span dimensions: `env` and
`component`. That is fine for LLM inference but completely blind to agent
decision quality. AgentLens adds a full semantic convention layer for agent
decision telemetry — the core demo thesis.

**Demo scenario:** An agent classifies a GitHub PR containing a SQL injection
vulnerability as "low_priority" with 0.92 confidence. Standard OTEL shows
HTTP 200. AgentLens shows `agent.eval.score = 0.1` and
`agent.eval.reason = "Missed SQL injection vulnerability in PR diff"`.

Working directory: `/Users/j_kanishkha/agentlens`

---

## Stack

| Layer | Technology |
|---|---|
| Agents | Python 3.11, LiteLLM (model routing) |
| Agent communication | A2A-style HTTP (FastAPI, agents expose `/run` endpoints) |
| Tool access | MCP server (GitHub PR fetcher via FastAPI + httpx) |
| Tracing | OpenTelemetry Python SDK → OTLP gRPC → Prove AI collector |
| Trace UI | Jaeger (added to Docker Compose) |
| Metrics | Prometheus + VictoriaMetrics (Prove AI's existing stack) |
| Dashboards | Grafana (added to Docker Compose, pre-provisioned dashboard JSON) |
| Env vars | .env file |

---

## Project Structure to Create

```
agentlens/
├── docker-compose.yml          # Extends Prove AI's stack
├── grafana/
│   └── dashboards/
│       └── agentlens.json      # Pre-built Grafana dashboard
├── agents/
│   ├── __init__.py
│   ├── triage_agent.py         # Agent 1: classifies PR priority
│   ├── security_agent.py       # Agent 2: scans for security issues
│   └── decision_agent.py       # Agent 3: final verdict + eval
├── mcp_server/
│   └── github_mcp.py           # FastAPI MCP server: fetches PR diff
├── otel_instrumentor.py        # Custom OTEL span attributes (already created)
├── orchestrator.py             # Runs the 3-agent pipeline
├── demo.py                     # Entry point: runs the SQL injection scenario
├── requirements.txt            # Already created
├── .env.example
└── README.md
```

---

## File 1: docker-compose.yml

Extend Prove AI's stack. Add Jaeger + Grafana. Do NOT redefine their existing
services (otel-collector, prometheus, victoriametrics, envoy) — just add new ones.

```yaml
version: "3.8"

services:
  # ── AgentLens services ──────────────────────────────────────────────────────

  mcp-server:
    build:
      context: .
      dockerfile: Dockerfile
    command: python mcp_server/github_mcp.py
    ports:
      - "8001:8001"
    env_file: .env
    networks:
      - agentlens

  triage-agent:
    build:
      context: .
      dockerfile: Dockerfile
    command: python agents/triage_agent.py
    ports:
      - "8010:8010"
    env_file: .env
    environment:
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - MCP_SERVER_URL=http://mcp-server:8001
      - SECURITY_AGENT_URL=http://security-agent:8011
    networks:
      - agentlens

  security-agent:
    build:
      context: .
      dockerfile: Dockerfile
    command: python agents/security_agent.py
    ports:
      - "8011:8011"
    env_file: .env
    environment:
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - MCP_SERVER_URL=http://mcp-server:8001
      - DECISION_AGENT_URL=http://decision-agent:8012
    networks:
      - agentlens

  decision-agent:
    build:
      context: .
      dockerfile: Dockerfile
    command: python agents/decision_agent.py
    ports:
      - "8012:8012"
    env_file: .env
    environment:
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
    networks:
      - agentlens

  # ── Observability additions ──────────────────────────────────────────────────

  jaeger:
    image: jaegertracing/all-in-one:1.57
    ports:
      - "16686:16686"   # Jaeger UI
      - "14317:4317"    # OTLP gRPC (separate port to avoid conflict)
    environment:
      - COLLECTOR_OTLP_ENABLED=true
    networks:
      - agentlens

  grafana:
    image: grafana/grafana:10.4.0
    ports:
      - "3001:3000"     # Port 3001 to avoid conflict if Prove AI uses 3000
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=agentlens
      - GF_AUTH_ANONYMOUS_ENABLED=true
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning
      - ./grafana/dashboards:/var/lib/grafana/dashboards
    networks:
      - agentlens

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.103.0
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol/config.yaml
    ports:
      - "4317:4317"
      - "4318:4318"
      - "8888:8888"
      - "8889:8889"
    networks:
      - agentlens

  prometheus:
    image: prom/prometheus:v2.52.0
    volumes:
      - ./prometheus.yaml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
    networks:
      - agentlens

  victoriametrics:
    image: victoriametrics/victoria-metrics:v1.101.0
    ports:
      - "8428:8428"
    command:
      - "--retentionPeriod=12"
    networks:
      - agentlens

networks:
  agentlens:
    driver: bridge
```

---

## File 2: otel-collector-config.yaml

This is the KEY file — extends Prove AI's config with agent-specific dimensions.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch: {}

connectors:
  spanmetrics:
    histogram:
      explicit:
        buckets: [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10]
    dimensions:
      # Prove AI's original 2 dimensions
      - name: env
      - name: component
      # AgentLens additions — agent decision telemetry
      - name: agent.name
      - name: agent.decision
      - name: agent.framework
      - name: agent.handoff.from
      - name: llm.model
    dimensions_cache_size: 1000

exporters:
  prometheus:
    endpoint: "0.0.0.0:8889"
    namespace: llm
    resource_to_telemetry_conversion:
      enabled: true
    enable_open_metrics: true
  otlp/jaeger:
    endpoint: "jaeger:4317"
    tls:
      insecure: true
  debug:
    verbosity: detailed

extensions:
  health_check:
    endpoint: 0.0.0.0:13133

service:
  extensions: [health_check]
  telemetry:
    metrics:
      level: detailed
      readers:
        - pull:
            exporter:
              prometheus:
                host: 0.0.0.0
                port: 8888
    logs:
      level: info
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [spanmetrics, otlp/jaeger, debug]
    metrics:
      receivers: [otlp, spanmetrics]
      processors: [batch]
      exporters: [prometheus, debug]
```

---

## File 3: prometheus.yaml

```yaml
global:
  scrape_interval: 5s

scrape_configs:
  - job_name: otel-collector
    static_configs:
      - targets: ["otel-collector:8889"]

remote_write:
  - url: http://victoriametrics:8428/api/v1/write
```

---

## File 4: Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
```

---

## File 5: mcp_server/github_mcp.py

A FastAPI server exposing one endpoint: `POST /fetch_pr`

- Accepts `{ "pr_url": "..." }` or `{ "use_mock": true }`
- If `use_mock: true`, return the hardcoded SQL injection PR diff below
- If real PR URL, use httpx + GITHUB_TOKEN to fetch the diff from GitHub API
- Returns `{ "title": "...", "diff": "...", "author": "...", "files_changed": N }`

**Mock SQL injection PR diff to return when `use_mock: true`:**
```
Title: "Add user search endpoint"
Author: "dev-contributor"
Files changed: 2

diff --git a/api/search.py b/api/search.py
+def search_users(query: str):
+    conn = get_db()
+    # Search users by name
+    result = conn.execute(f"SELECT * FROM users WHERE name = '{query}'")
+    return result.fetchall()
```

Port: 8001

---

## File 6: agents/triage_agent.py

FastAPI server on port 8010. Exposes `POST /run`.

**What it does:**
1. Receives `{ "pr_url": "...", "use_mock": bool }`
2. Fetches PR diff from MCP server at `MCP_SERVER_URL/fetch_pr`
3. Uses LiteLLM to call `gpt-4o-mini` (or `gemini/gemini-2.0-flash` if GEMINI_API_KEY set) with prompt:
   ```
   You are a PR triage agent. Given this PR diff, classify its priority.
   Priority options: critical, high, medium, low_priority
   Respond with JSON: {"priority": "...", "reasoning": "...", "confidence": 0.0-1.0, "contains_code_changes": true/false}
   
   PR: {diff}
   ```
4. Wraps the entire work in an `agent_span` from otel_instrumentor.py:
   - `agent.name = "triage_agent"`
   - Records the LLM call
   - Records decision = priority, confidence from LLM response
5. If `contains_code_changes` is true, calls `SECURITY_AGENT_URL/run` with the PR data
6. Returns the triage result

Import `agent_span`, `setup_tracing` from `otel_instrumentor` (parent directory).
Use `sys.path.insert(0, '/app')` for imports.

---

## File 7: agents/security_agent.py

FastAPI server on port 8011. Exposes `POST /run`.

**What it does:**
1. Receives `{ "pr_data": {...}, "diff": "..." }`
2. Uses LiteLLM to call `gpt-4o-mini` with prompt:
   ```
   You are a security review agent. Scan this PR diff for security vulnerabilities.
   Focus on: SQL injection, XSS, hardcoded secrets, path traversal, command injection.
   
   Respond with JSON: {
     "has_vulnerabilities": bool,
     "severity": "critical|high|medium|low|none",
     "findings": ["..."],
     "reasoning": "...",
     "confidence": 0.0-1.0
   }
   
   PR diff: {diff}
   ```
3. Wraps in `agent_span`:
   - `agent.name = "security_agent"`
   - `agent.handoff.from = "triage_agent"`
   - `agent.handoff.reason = "contains_code_changes"`
   - Records the LLM call
   - Records decision = severity, confidence from LLM
4. Forwards to `DECISION_AGENT_URL/run` with combined context
5. Returns security findings

---

## File 8: agents/decision_agent.py

FastAPI server on port 8012. Exposes `POST /run`.

**What it does:**
1. Receives `{ "triage_result": {...}, "security_result": {...}, "diff": "..." }`
2. Uses LiteLLM to synthesize a final decision:
   ```
   You are the final decision agent for PR triage.
   Given triage and security analysis, make a final verdict.
   
   Respond with JSON: {
     "final_priority": "critical|high|medium|low_priority",
     "action": "block|review|merge",
     "summary": "...",
     "confidence": 0.0-1.0
   }
   
   Triage: {triage_result}
   Security: {security_result}
   ```
3. **IMPORTANT — LLM-as-Judge eval step:**
   After getting the final decision, run a SECOND LLM call as evaluator:
   ```
   You are an expert code reviewer evaluating an AI agent's PR triage decision.
   
   The agent decided: {final_decision}
   The actual PR diff contains: {diff}
   
   Score the decision quality from 0.0 (completely wrong) to 1.0 (perfect).
   Respond with JSON: {"eval_score": 0.0-1.0, "eval_reason": "..."}
   ```
4. Wraps in `agent_span`:
   - `agent.name = "decision_agent"`
   - `agent.handoff.from = "security_agent"`
   - Records both LLM calls (decision + eval)
   - Records decision, confidence, eval_score, eval_reason
5. Returns final verdict

---

## File 9: orchestrator.py

Simple Python script (not a server). Runs the full pipeline:

```python
import httpx
import json

def run_pipeline(use_mock: bool = True, pr_url: str = None):
    """Run the full AgentLens triage pipeline."""
    print("=" * 60)
    print("AgentLens — Multi-Agent PR Triage")
    print("=" * 60)
    
    payload = {"use_mock": use_mock}
    if pr_url:
        payload["pr_url"] = pr_url
    
    print("\n[1/3] Triage Agent → analyzing PR...")
    response = httpx.post("http://localhost:8010/run", json=payload, timeout=60)
    result = response.json()
    
    print(f"      Decision: {result.get('final_priority', result.get('priority'))}")
    print(f"      Confidence: {result.get('confidence')}")
    print(f"      Eval Score: {result.get('eval_score', 'N/A')}")
    
    if result.get('eval_score') and result['eval_score'] < 0.3:
        print(f"\n⚠️  AgentLens ALERT: Low eval score ({result['eval_score']})")
        print(f"   Reason: {result.get('eval_reason')}")
        print(f"   Standard OTEL shows: HTTP 200, latency OK")
        print(f"   AgentLens shows: Decision quality POOR")
    
    print("\n📊 View traces: http://localhost:16686 (Jaeger)")
    print("📈 View metrics: http://localhost:3001 (Grafana)")
    return result

if __name__ == "__main__":
    run_pipeline(use_mock=True)
```

---

## File 10: demo.py

Entry point that demonstrates the "confidently wrong agent" scenario:

```python
"""
AgentLens Demo — The SQL Injection Scenario

Standard observability shows: HTTP 200, 1.2s latency, 4200 tokens
AgentLens shows: agent.eval.score=0.1, "Missed SQL injection vulnerability"

This is the gap in Prove AI's current OTEL config.
Their spanmetrics connector only tracks env + component.
AgentLens adds 5 more agent-specific dimensions including agent.eval.score.
"""

import time
from orchestrator import run_pipeline

print("""
╔══════════════════════════════════════════════════════════════╗
║              AgentLens — Agent Decision Telemetry            ║
║         Extending Prove AI's observability-pipeline          ║
╚══════════════════════════════════════════════════════════════╝

Scenario: PR "Add user search endpoint" contains SQL injection.
          Agent classifies it as LOW PRIORITY with 0.92 confidence.
          Standard OTEL: green. AgentLens: eval_score=0.1.
""")

time.sleep(1)
result = run_pipeline(use_mock=True)

print("""
═══════════════════════════════════════════════════════════════
What Prove AI's current OTEL sees:
  • spans: ✓  latency: 1.2s  status: 200  tokens: 4200

What AgentLens adds:
  • agent.name: decision_agent
  • agent.decision: low_priority
  • agent.confidence: 0.92
  • agent.eval.score: 0.1          ← THE SIGNAL THAT MATTERS
  • agent.eval.reason: Missed SQL injection vulnerability
  • agent.handoff.from: security_agent
  • llm.model: gpt-4o-mini
═══════════════════════════════════════════════════════════════
""")
```

---

## File 11: .env.example

```
# LLM (at least one required)
OPENAI_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here

# GitHub (optional — demo works with mock data)
GITHUB_TOKEN=your_token_here

# OTEL
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

---

## File 12: grafana/provisioning/datasources/prometheus.yaml

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://prometheus:9090
    isDefault: true
  - name: VictoriaMetrics
    type: prometheus
    url: http://victoriametrics:8428
```

---

## File 13: grafana/provisioning/dashboards/dashboard.yaml

```yaml
apiVersion: 1
providers:
  - name: agentlens
    folder: AgentLens
    type: file
    options:
      path: /var/lib/grafana/dashboards
```

---

## File 14: grafana/dashboards/agentlens.json

Create a Grafana dashboard JSON with these 4 panels, all using the `Prometheus`
datasource querying metrics with prefix `llm_` (Prove AI's namespace):

**Panel 1 — Agent Decision Quality (Gauge)**
- Title: "Avg Eval Score (last 5m)"
- Query: `avg(llm_calls_total{agent_name=~".+"}) by (agent_name)`
- Thresholds: red < 0.3, yellow < 0.7, green >= 0.7

**Panel 2 — Agent Calls by Decision (Bar chart)**
- Title: "Decisions by Agent"
- Query: `sum(llm_calls_total) by (agent_name, agent_decision)`

**Panel 3 — Latency by Agent (Time series)**
- Title: "Agent Latency p95"
- Query: `histogram_quantile(0.95, sum(rate(llm_duration_milliseconds_bucket[5m])) by (le, agent_name))`

**Panel 4 — Confidently Wrong Alerts (Stat)**
- Title: "High Confidence Bad Decisions"
- Description: "Decisions with confidence > 0.8 but eval_score < 0.3"
- This is the money panel — shows AgentLens value vs standard OTEL

---

## File 15: README.md

Write a concise README with:
1. One-line description
2. The core thesis (2 sentences): Prove AI's OTEL has 2 dimensions. Agents need 7.
3. Quick start: `cp .env.example .env`, set API key, `docker compose up`, `python demo.py`
4. What you'll see: Jaeger URL, Grafana URL
5. The 7 custom span attributes added by AgentLens

---

## Build Instructions for Codex

1. Create ALL files listed above in `/Users/j_kanishkha/agentlens/`
2. Use `os.getenv()` for all config — no hardcoded values
3. In each agent, handle LiteLLM errors gracefully with try/except
4. LiteLLM model selection: check `OPENAI_API_KEY` first, fall back to `GEMINI_API_KEY` with `gemini/gemini-2.0-flash`
5. All FastAPI servers use `uvicorn.run(app, host="0.0.0.0", port=PORT)` at bottom
6. The otel_instrumentor.py is already created — import from it, do not recreate it
7. After creating all files, run `pip install -r requirements.txt` to verify deps resolve

## Validation
After building, confirm these files exist:
- docker-compose.yml
- otel-collector-config.yaml
- prometheus.yaml
- Dockerfile
- mcp_server/github_mcp.py
- agents/triage_agent.py
- agents/security_agent.py
- agents/decision_agent.py
- orchestrator.py
- demo.py
- .env.example
- grafana/provisioning/datasources/prometheus.yaml
- grafana/provisioning/dashboards/dashboard.yaml
- grafana/dashboards/agentlens.json
- README.md
