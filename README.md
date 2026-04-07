# AgentLens

AgentLens is an observability demo for AI agents doing GitHub PR triage.

The point is simple: a normal system can tell you that an agent ran, returned `200`,
and finished in a reasonable amount of time. That still does not tell you whether
the agent made a good decision.

AgentLens adds decision-quality telemetry on top of a standard OpenTelemetry stack.
Instead of only tracking request health, it records:

- what the agent decided
- how confident it was
- whether that decision was actually good
- why the decision was judged good or bad
- how context moved between agents

This project was built as a demo extension of Prove AI's
[`observability-pipeline`](https://github.com/prove-ai/observability-pipeline).

## What Problem This Solves

Traditional observability answers questions like:

- Did the request succeed?
- How long did it take?
- Which service handled it?
- How many tokens did the model use?

For AI agents, that is not enough.

An agent can:

- return success
- respond quickly
- look healthy in dashboards
- and still make a wrong decision with high confidence

AgentLens is meant to surface that gap.

Example:

- PR contains a SQL injection issue
- agent says `low_priority`
- confidence is `0.92`
- request status is `200`

Operationally green. Decisionally bad.

That is the signal AgentLens is built to expose.

## Architecture

AgentLens uses simple Python services plus OpenTelemetry.

### Runtime flow

1. A PR diff is fetched from GitHub, or loaded from a built-in mock scenario.
2. `triage-agent` classifies the PR.
3. `security-agent` reviews it for security concerns.
4. `decision-agent` makes the final decision and scores the quality of that decision.
5. OpenTelemetry exports spans and metrics into the observability stack.

### Stack

- `FastAPI` for agent services
- `LiteLLM` for model calls
- `httpx` for service-to-service communication
- `OpenTelemetry` for traces and metrics
- `Jaeger` for trace inspection
- `Prometheus` for metrics scraping
- `Grafana` for metric dashboards
- `VictoriaMetrics` for storage-compatible metrics backend
- `Docker Compose` for local orchestration

### Mental model

- `Jaeger` shows individual runs and their span metadata
- `Prometheus` stores time-series metrics
- `Grafana` visualizes those metrics over time
- the OTEL collector is the pipe between the services and the observability tools

## Repo Structure

```text
agentlens/
├── agents/
│   ├── triage_agent.py
│   ├── security_agent.py
│   └── decision_agent.py
├── mcp_server/
│   └── github_mcp.py
├── grafana/
│   ├── dashboards/
│   └── provisioning/
├── demo.py
├── orchestrator.py
├── otel_instrumentor.py
├── otel-collector-config.yaml
├── prometheus.yaml
├── docker-compose.yml
└── tests/
```

### Important files

- `demo.py`
  Entry point for both the controlled demo and real GitHub PR analysis.

- `orchestrator.py`
  Calls the agent pipeline and prints the result.

- `mcp_server/github_mcp.py`
  Fetches a real GitHub PR diff, or returns a built-in mock PR for the demo.

- `agents/triage_agent.py`
  First-stage PR classification.

- `agents/security_agent.py`
  Security-focused review step.

- `agents/decision_agent.py`
  Final verdict plus decision-quality evaluation.

- `otel_instrumentor.py`
  Core instrumentation layer. This is where decision telemetry gets attached to spans
  and exported as metrics.

## Telemetry Signals

AgentLens records trace attributes such as:

- `agent.name`
- `agent.decision`
- `agent.confidence`
- `agent.eval.score`
- `agent.eval.reason`
- `agent.handoff.from`
- `llm.model`

It also exports metrics such as:

- decision counts
- bad decision counts
- confidence distribution
- eval score distribution
- latency distribution

## Quick Start

```bash
cd /Users/j_kanishkha/agentlens
cp .env.example .env
docker compose up -d
```

### Browser URLs

- Jaeger: `http://localhost:16686`
- Grafana: `http://localhost:3001`
- Prometheus: `http://localhost:9090`

## Demo Modes

There are two useful ways to demo AgentLens.

### 1. Real PR mode

Use this to prove the system works on a real GitHub pull request.

```bash
python3 demo.py --pr-url https://github.com/<owner>/<repo>/pull/<number>
```

Example:

```bash
python3 demo.py --pr-url https://github.com/prove-ai/observability-pipeline/pull/2
```

Notes:

- public PRs work without a GitHub token
- private repos require `GITHUB_TOKEN`
- real runs depend on live model output, so the exact result can vary

### 2. Controlled failure demo

Use this to show the exact failure mode the project is about.

```bash
python3 demo.py
```

This mode is intentionally deterministic.
It simulates a realistic case where the agent is confidently wrong, so the
decision-quality telemetry can be demonstrated cleanly every time.

This is not “fake infra.” The telemetry pipeline is still real:

- real services
- real OTEL traces
- real metrics
- real Jaeger/Grafana flow

Only the scenario is controlled.

## Recommended Interview Demo Flow

Use this order:

1. Run a real PR analysis
2. Run the controlled failure case
3. Show Jaeger
4. Show Grafana

### Commands

```bash
python3 demo.py --pr-url https://github.com/prove-ai/observability-pipeline/pull/2
python3 demo.py
```

### What to say

Start with:

> I built this to extend AI observability from request health to decision quality.

For the real PR:

> First, here it is running on a real PR from your public repo.

For the controlled replay:

> Second, here is the failure mode I care about: the workflow succeeds, but the decision quality is poor.

Main takeaway:

> Traditional observability tells me the agent ran. AgentLens tells me whether the agent was right.

## What to Show in Jaeger

In Jaeger, the best trace to open is usually the `decision-agent` trace.

Point out:

- `agent.decision`
- `agent.confidence`
- `agent.eval.score`
- `agent.eval.reason`
- `agent.handoff.from`
- `llm.decision.model`

What this proves:

- the trace is real
- the final decision is visible
- the quality of the decision is visible
- the reasoning handoff between agents is visible

## What to Show in Grafana

In Grafana, focus on:

- average eval score
- decisions by agent
- latency by agent

Do not over-explain the dashboard.
The purpose is just to show that the same trace-level signal is now queryable as
time-series metrics.

## Environment Variables

Example `.env.example`:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `GITHUB_TOKEN`
- `OTEL_EXPORTER_OTLP_ENDPOINT`

### Notes

- mock demo mode does not require an LLM key
- real PR mode requires an LLM key
- public real PR mode does not require `GITHUB_TOKEN`
- private repo access requires `GITHUB_TOKEN`

## Tests

Run:

```bash
python3 -m unittest discover -s tests -v
```

Current tests cover:

- diff truncation behavior
- decision evaluation logic for the controlled SQL injection scenario

## Why This Is Connected to Prove AI

This demo was built around the same observability idea Prove AI is working on,
but extended for agent systems.

The claim is:

- existing telemetry is good at telling you whether the system ran
- agent systems also need telemetry that tells you whether the system made a good decision

That is the gap AgentLens is trying to make visible.

## Publish

GitHub repo:

- `https://github.com/Jkanishkha0305/agentlens-otel-demo`

## One-Line Summary

AgentLens is an observability layer for AI agent decision quality, built around
GitHub PR triage and instrumented with OpenTelemetry, Jaeger, Prometheus, and Grafana.
