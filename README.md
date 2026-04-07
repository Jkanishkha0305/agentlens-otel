# AgentLens

AgentLens is a multi-agent GitHub PR triage demo that adds decision-quality telemetry on top of OpenTelemetry.

Prove AI's current OTEL setup only tracks two useful span dimensions for this workflow: `env` and `component`. AgentLens extends that with seven decision-oriented span attributes so you can see when an agent was confidently wrong, not just whether the request returned `200`.

## Quick Start

```bash
cp .env.example .env
```

For the deterministic mock demo, you can start the stack immediately. Real PR mode
requires at least one LLM API key plus a GitHub token.

```bash
docker compose up -d
python demo.py
```

`python demo.py` uses the built-in mock PR and a deterministic "confidently wrong"
agent path so the telemetry story is stable every time. Real PR runs can still use
the live model path through the agent HTTP endpoints.

## What You'll See

- Jaeger: `http://localhost:16686`
- Grafana: `http://localhost:3001`

## AgentLens Span Attributes

- `agent.name`
- `agent.decision`
- `agent.framework`
- `agent.handoff.from`
- `agent.confidence`
- `agent.eval.score`
- `agent.eval.reason`
