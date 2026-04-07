"""
AgentLens — Custom OTEL Semantic Conventions for Agent Decision Telemetry

This is the core IP. Prove AI's existing OTEL collector has only 2 span dimensions:
  - env
  - component

That's enough for LLM inference monitoring. For multi-agent systems, it's not.
When an agent makes a bad decision in production, those 2 dimensions can't tell you:
  - Was the reasoning sound?
  - What was the confidence level?
  - Where did context break down in the handoff?
  - Was the decision actually correct?

AgentLens adds a full semantic convention layer for agent decision telemetry.
"""

import time
from contextlib import contextmanager
from typing import Any, Optional

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_DECISION_COUNTER = None
_BAD_DECISION_COUNTER = None
_EVAL_SCORE_HISTOGRAM = None
_CONFIDENCE_HISTOGRAM = None
_LATENCY_HISTOGRAM = None

# ── Bootstrap ────────────────────────────────────────────────────────────────

def setup_tracing(
    service_name: str = "agentlens",
    otlp_endpoint: str = "http://localhost:4317",
) -> trace.Tracer:
    """
    Configure the OTEL tracer to export to:
      1. Prove AI's observability-pipeline (extends their collector)
      2. Jaeger (added to their Docker Compose for trace visualisation)
    """
    resource = Resource.create({
        "service.name": service_name,
        "env": "demo",
        "component": "agentlens",  # Prove AI's existing dimensions
    })

    provider = TracerProvider(resource=resource)
    trace_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    trace.set_tracer_provider(provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True),
        export_interval_millis=2000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    meter = metrics.get_meter(service_name)

    global _DECISION_COUNTER
    global _BAD_DECISION_COUNTER
    global _EVAL_SCORE_HISTOGRAM
    global _CONFIDENCE_HISTOGRAM
    global _LATENCY_HISTOGRAM

    _DECISION_COUNTER = meter.create_counter(
        "agentlens_decisions",
        description="Count of agent decisions emitted by AgentLens.",
    )
    _BAD_DECISION_COUNTER = meter.create_counter(
        "agentlens_bad_decisions",
        description="Count of high-confidence agent decisions with poor evaluation scores.",
    )
    _EVAL_SCORE_HISTOGRAM = meter.create_histogram(
        "agentlens_eval_score",
        unit="1",
        description="Distribution of agent decision evaluation scores.",
    )
    _CONFIDENCE_HISTOGRAM = meter.create_histogram(
        "agentlens_confidence",
        unit="1",
        description="Distribution of agent confidence scores.",
    )
    _LATENCY_HISTOGRAM = meter.create_histogram(
        "agentlens_decision_latency_ms",
        unit="ms",
        description="Distribution of end-to-end agent decision latency in milliseconds.",
    )

    return trace.get_tracer(service_name)


# ── Agent Decision Span Context Manager ──────────────────────────────────────

@contextmanager
def agent_span(
    tracer: trace.Tracer,
    agent_name: str,
    agent_framework: str = "custom",
    handoff_from: Optional[str] = None,
    handoff_reason: Optional[str] = None,
    handoff_context_bytes: Optional[int] = None,
):
    """
    Context manager that wraps an agent's work in an OTEL span
    and provides a helper to record the decision + evaluation.

    Usage:
        with agent_span(tracer, "security_reviewer") as span_helper:
            result = agent.run(input)
            span_helper.record_decision(
                decision="low_priority",
                confidence=0.92,
                reasoning="No obvious security issues in diff",
                eval_score=0.1,
                eval_reason="Missed SQL injection on line 47",
            )
    """
    with tracer.start_as_current_span(f"agent.{agent_name}") as span:
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("agent.framework", agent_framework)

        metric_attributes = {
            "agent_name": agent_name,
            "agent_framework": agent_framework,
        }

        if handoff_from:
            span.set_attribute("agent.handoff.from", handoff_from)
            metric_attributes["agent_handoff_from"] = handoff_from
        if handoff_reason:
            span.set_attribute("agent.handoff.reason", handoff_reason)
            metric_attributes["agent_handoff_reason"] = handoff_reason
        if handoff_context_bytes:
            span.set_attribute("agent.handoff.context_bytes", handoff_context_bytes)
            metric_attributes["agent_handoff_context_bytes"] = handoff_context_bytes

        helper = _SpanHelper(span, metric_attributes)
        yield helper


class _SpanHelper:
    """Attached to a span; call record_decision() once the agent finishes."""

    def __init__(self, span: trace.Span, metric_attributes: dict[str, Any]):
        self._span = span
        self._start = time.time()
        self._metric_attributes = metric_attributes

    def record_llm_call(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
    ):
        """Record the LLM call within this agent's span."""
        self._span.set_attribute("llm.model", model)
        self._span.set_attribute("llm.prompt_tokens", prompt_tokens)
        self._span.set_attribute("llm.completion_tokens", completion_tokens)
        self._span.set_attribute("llm.total_tokens", prompt_tokens + completion_tokens)
        self._span.set_attribute("llm.total_cost_usd", cost_usd)

    def record_tool_use(self, tool_name: str, result_tokens: int):
        """Record a tool call within this agent's span."""
        self._span.set_attribute("tool.name", tool_name)
        self._span.set_attribute("tool.result_tokens", result_tokens)

    def record_decision(
        self,
        decision: str,
        confidence: float,
        reasoning: str,
        memory_utilization: float = 0.0,
        eval_score: Optional[float] = None,
        eval_reason: Optional[str] = None,
    ):
        """
        Record the agent's final decision + quality score.

        eval_score: 0.0 (very bad) to 1.0 (perfect) — from LLM-as-judge
        eval_reason: why the judge gave that score

        This is the span attribute that Prove AI's current pipeline is missing.
        Standard OTEL shows latency + tokens. This shows whether the decision was CORRECT.
        """
        elapsed = time.time() - self._start

        self._span.set_attribute("agent.decision", decision)
        self._span.set_attribute("agent.confidence", confidence)
        self._span.set_attribute("agent.reasoning", reasoning)
        self._span.set_attribute("agent.memory_utilization", memory_utilization)
        self._span.set_attribute("agent.latency_ms", int(elapsed * 1000))

        metric_attributes = {
            **self._metric_attributes,
            "agent_decision": decision,
        }

        if _DECISION_COUNTER is not None:
            _DECISION_COUNTER.add(1, metric_attributes)
        if _CONFIDENCE_HISTOGRAM is not None:
            _CONFIDENCE_HISTOGRAM.record(confidence, metric_attributes)
        if _LATENCY_HISTOGRAM is not None:
            _LATENCY_HISTOGRAM.record(elapsed * 1000, metric_attributes)

        if eval_score is not None:
            self._span.set_attribute("agent.eval.score", eval_score)
            metric_attributes["decision_quality"] = "bad" if confidence > 0.8 and eval_score < 0.3 else "good"
            if _EVAL_SCORE_HISTOGRAM is not None:
                _EVAL_SCORE_HISTOGRAM.record(eval_score, metric_attributes)
            if confidence > 0.8 and eval_score < 0.3 and _BAD_DECISION_COUNTER is not None:
                _BAD_DECISION_COUNTER.add(1, metric_attributes)
        if eval_reason is not None:
            self._span.set_attribute("agent.eval.reason", eval_reason)
