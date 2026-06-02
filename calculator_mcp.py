#!/usr/bin/env python3
"""
Token Budget Estimator & Cost Calculator MCP Server
====================================================

MCP tool server for Claude Code that estimates costs for Bedrock model
usage. Developers ask Claude "how much will this cost if I process
1000 files?" and this tool calculates based on model, token counts,
and current pricing.

OTEL Stream 2 Telemetry (auto-captured on every invocation):
─────────────────────────────────────────────────────────────
  • tool_name: "estimate_cost" | "compare_models" | "budget_check"
  • duration_ms: calculation time (fast, but still tracked)
  • status: pass/fail
  • Cost estimation usage itself demonstrates ROI awareness:
    - Teams that check costs before running are cost-optimized
    - Estimation frequency correlates with lower Bedrock spend
    - These events flow → CloudWatch → QuickSight ROI Dashboard

Add to ~/.claude/mcp.json to enable in Claude Code.
"""

# ---------------------------------------------------------------------------
# Auto-install dependencies
# ---------------------------------------------------------------------------
import subprocess, sys

def _ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

_ensure("mcp", "mcp")

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import json
import time
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ---------------------------------------------------------------------------
# Bedrock Model Pricing (per 1K tokens, USD) — April 2026
# ---------------------------------------------------------------------------
# Source: https://aws.amazon.com/bedrock/pricing/
# Prices are approximate — update as AWS publishes changes.

MODEL_PRICING = {
    # ── Claude 4 Family ──
    "claude-4-opus": {
        "display_name": "Claude 4 Opus",
        "model_id": "anthropic.claude-4-opus-20260401-v1:0",
        "input_per_1k": 0.015,
        "output_per_1k": 0.075,
        "context_window": 200_000,
        "max_output": 4_096,
        "tier": "premium",
        "best_for": "Complex reasoning, analysis, multi-step coding, research",
    },
    "claude-4-sonnet": {
        "display_name": "Claude 4 Sonnet",
        "model_id": "anthropic.claude-4-sonnet-20260401-v1:0",
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
        "context_window": 200_000,
        "max_output": 8_192,
        "tier": "balanced",
        "best_for": "General purpose, code generation, chat, most production workloads",
    },

    # ── Claude 3.5 Family ──
    "claude-3.5-sonnet": {
        "display_name": "Claude 3.5 Sonnet v2",
        "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
        "context_window": 200_000,
        "max_output": 8_192,
        "tier": "balanced",
        "best_for": "Code generation, analysis, production workloads",
    },
    "claude-3.5-haiku": {
        "display_name": "Claude 3.5 Haiku",
        "model_id": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "input_per_1k": 0.0008,
        "output_per_1k": 0.004,
        "context_window": 200_000,
        "max_output": 8_192,
        "tier": "fast",
        "best_for": "Classification, extraction, simple tasks, high throughput",
    },

    # ── Claude 3 Family ──
    "claude-3-opus": {
        "display_name": "Claude 3 Opus",
        "model_id": "anthropic.claude-3-opus-20240229-v1:0",
        "input_per_1k": 0.015,
        "output_per_1k": 0.075,
        "context_window": 200_000,
        "max_output": 4_096,
        "tier": "premium",
        "best_for": "Most complex tasks, expert-level analysis (legacy — prefer Claude 4 Opus)",
    },
    "claude-3-sonnet": {
        "display_name": "Claude 3 Sonnet",
        "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
        "context_window": 200_000,
        "max_output": 4_096,
        "tier": "balanced",
        "best_for": "Balanced performance (legacy — prefer Claude 3.5 Sonnet)",
    },
    "claude-3-haiku": {
        "display_name": "Claude 3 Haiku",
        "model_id": "anthropic.claude-3-haiku-20240307-v1:0",
        "input_per_1k": 0.00025,
        "output_per_1k": 0.00125,
        "context_window": 200_000,
        "max_output": 4_096,
        "tier": "fast",
        "best_for": "Ultra-fast, ultra-cheap, simple tasks (legacy — prefer Claude 3.5 Haiku)",
    },

    # ── Amazon Titan ──
    "titan-text-express": {
        "display_name": "Amazon Titan Text Express",
        "model_id": "amazon.titan-text-express-v1",
        "input_per_1k": 0.0002,
        "output_per_1k": 0.0006,
        "context_window": 8_192,
        "max_output": 4_096,
        "tier": "economy",
        "best_for": "Simple text tasks, low cost",
    },
    "titan-text-premier": {
        "display_name": "Amazon Titan Text Premier",
        "model_id": "amazon.titan-text-premier-v1:0",
        "input_per_1k": 0.0005,
        "output_per_1k": 0.0015,
        "context_window": 32_000,
        "max_output": 4_096,
        "tier": "economy",
        "best_for": "General text tasks at lower cost",
    },

    # ── Meta Llama ──
    "llama-3.1-70b": {
        "display_name": "Meta Llama 3.1 70B Instruct",
        "model_id": "meta.llama3-1-70b-instruct-v1:0",
        "input_per_1k": 0.00099,
        "output_per_1k": 0.00099,
        "context_window": 128_000,
        "max_output": 4_096,
        "tier": "balanced",
        "best_for": "Open-source alternative, code, multilingual",
    },
    "llama-3.1-8b": {
        "display_name": "Meta Llama 3.1 8B Instruct",
        "model_id": "meta.llama3-1-8b-instruct-v1:0",
        "input_per_1k": 0.00022,
        "output_per_1k": 0.00022,
        "context_window": 128_000,
        "max_output": 4_096,
        "tier": "fast",
        "best_for": "Fast and cheap open-source, simple tasks",
    },

    # ── Mistral ──
    "mistral-large": {
        "display_name": "Mistral Large 2",
        "model_id": "mistral.mistral-large-2407-v1:0",
        "input_per_1k": 0.002,
        "output_per_1k": 0.006,
        "context_window": 128_000,
        "max_output": 4_096,
        "tier": "balanced",
        "best_for": "Coding, reasoning, multilingual",
    },
}

# Aliases for convenience
MODEL_ALIASES = {
    "opus": "claude-4-opus",
    "sonnet": "claude-4-sonnet",
    "haiku": "claude-3.5-haiku",
    "claude-4": "claude-4-sonnet",
    "claude-3.5": "claude-3.5-sonnet",
    "claude-3": "claude-3-sonnet",
    "claude": "claude-4-sonnet",
    "titan": "titan-text-premier",
    "llama": "llama-3.1-70b",
    "mistral": "mistral-large",
}

# ---------------------------------------------------------------------------
# Estimation Helpers
# ---------------------------------------------------------------------------

# Rough token-per-character ratio (English text)
CHARS_PER_TOKEN = 4.0
WORDS_PER_TOKEN = 0.75

def _resolve_model(model: str) -> tuple[str, dict]:
    """Resolve model name/alias to pricing entry."""
    key = model.lower().strip()
    key = MODEL_ALIASES.get(key, key)
    if key in MODEL_PRICING:
        return key, MODEL_PRICING[key]
    # Fuzzy match
    for k, v in MODEL_PRICING.items():
        if key in k or key in v["model_id"].lower() or key in v["display_name"].lower():
            return k, v
    raise ValueError(
        f"Unknown model: '{model}'. Available: {', '.join(sorted(MODEL_PRICING.keys()))} "
        f"(aliases: {', '.join(sorted(MODEL_ALIASES.keys()))})"
    )


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    requests: int = 1,
) -> dict:
    """Estimate cost for a model invocation."""
    key, pricing = _resolve_model(model)

    input_cost = (input_tokens / 1000) * pricing["input_per_1k"] * requests
    output_cost = (output_tokens / 1000) * pricing["output_per_1k"] * requests
    total_cost = input_cost + output_cost

    return {
        "model": pricing["display_name"],
        "model_id": pricing["model_id"],
        "tier": pricing["tier"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "requests": requests,
        "total_tokens_per_request": input_tokens + output_tokens,
        "total_tokens_all_requests": (input_tokens + output_tokens) * requests,
        "pricing": {
            "input_per_1k_tokens": f"${pricing['input_per_1k']:.5f}",
            "output_per_1k_tokens": f"${pricing['output_per_1k']:.5f}",
        },
        "cost_breakdown": {
            "input_cost": f"${input_cost:.6f}",
            "output_cost": f"${output_cost:.6f}",
            "total_cost": f"${total_cost:.6f}",
        },
        "cost_usd": round(total_cost, 6),
        "context_window": pricing["context_window"],
        "max_output_tokens": pricing["max_output"],
        "fits_in_context": input_tokens <= pricing["context_window"],
        "fits_output": output_tokens <= pricing["max_output"],
        "best_for": pricing["best_for"],
    }


def compare_models(
    input_tokens: int,
    output_tokens: int,
    requests: int = 1,
    tier_filter: Optional[str] = None,
) -> list[dict]:
    """Compare cost across all available models."""
    results = []
    for key, pricing in MODEL_PRICING.items():
        if tier_filter and pricing["tier"] != tier_filter:
            continue
        input_cost = (input_tokens / 1000) * pricing["input_per_1k"] * requests
        output_cost = (output_tokens / 1000) * pricing["output_per_1k"] * requests
        total = input_cost + output_cost
        results.append({
            "model": pricing["display_name"],
            "model_key": key,
            "tier": pricing["tier"],
            "total_cost_usd": round(total, 6),
            "total_cost_display": f"${total:.4f}",
            "input_cost": f"${input_cost:.4f}",
            "output_cost": f"${output_cost:.4f}",
            "context_window": pricing["context_window"],
            "fits": input_tokens <= pricing["context_window"],
            "best_for": pricing["best_for"],
        })

    results.sort(key=lambda x: x["total_cost_usd"])
    return results


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
server = Server("calculator-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="estimate_cost",
            description=(
                "Estimate the cost of a Bedrock model invocation. Provide model name "
                "and token counts to get cost breakdown. Supports all Claude models "
                "(Haiku, Sonnet, Opus), Titan, Llama, Mistral. "
                "Use when a developer asks 'how much will this cost?', 'estimate tokens', "
                "'what's the price for processing N files?'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": (
                            "Model name or alias. Examples: 'claude-4-sonnet', 'haiku', 'opus', "
                            "'claude-3.5-sonnet', 'titan', 'llama', 'mistral-large'"
                        ),
                    },
                    "input_tokens": {
                        "type": "integer",
                        "description": "Estimated input tokens per request",
                    },
                    "output_tokens": {
                        "type": "integer",
                        "description": "Estimated output tokens per request",
                    },
                    "requests": {
                        "type": "integer",
                        "description": "Number of requests/invocations (default: 1)",
                        "default": 1,
                    },
                    "input_text_chars": {
                        "type": "integer",
                        "description": "Alternative: character count of input text (auto-converts to tokens at ~4 chars/token)",
                    },
                    "input_text_words": {
                        "type": "integer",
                        "description": "Alternative: word count of input text (auto-converts to tokens at ~0.75 words/token)",
                    },
                },
                "required": ["model"],
            },
        ),
        Tool(
            name="compare_models",
            description=(
                "Compare costs across all available Bedrock models for the same workload. "
                "Shows cost from cheapest to most expensive with model capabilities. "
                "Helps developers pick the right model for their budget and needs. "
                "Use when developer asks 'which model is cheapest?', 'compare prices', "
                "'what's the best model for my budget?'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "input_tokens": {
                        "type": "integer",
                        "description": "Estimated input tokens per request",
                    },
                    "output_tokens": {
                        "type": "integer",
                        "description": "Estimated output tokens per request",
                    },
                    "requests": {
                        "type": "integer",
                        "description": "Number of requests (default: 1)",
                        "default": 1,
                    },
                    "tier": {
                        "type": "string",
                        "description": "Filter by tier: 'premium', 'balanced', 'fast', 'economy'",
                        "enum": ["premium", "balanced", "fast", "economy"],
                    },
                },
                "required": ["input_tokens", "output_tokens"],
            },
        ),
        Tool(
            name="budget_check",
            description=(
                "Check if a workload fits within a budget. Given a model, token estimates, "
                "number of requests, and monthly budget, returns whether it fits and "
                "suggests alternatives if over budget. "
                "Use when developer asks 'can I afford to run this?', 'will this stay under $X?'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Model name or alias",
                    },
                    "input_tokens": {
                        "type": "integer",
                        "description": "Input tokens per request",
                    },
                    "output_tokens": {
                        "type": "integer",
                        "description": "Output tokens per request",
                    },
                    "requests_per_day": {
                        "type": "integer",
                        "description": "Expected requests per day",
                    },
                    "monthly_budget_usd": {
                        "type": "number",
                        "description": "Monthly budget in USD",
                    },
                },
                "required": ["model", "input_tokens", "output_tokens",
                             "requests_per_day", "monthly_budget_usd"],
            },
        ),
        Tool(
            name="list_models",
            description=(
                "List all supported Bedrock models with their pricing, capabilities, "
                "and recommended use cases. Use when developer asks 'what models are available?', "
                "'show me the pricing', 'which models does Bedrock support?'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tier": {
                        "type": "string",
                        "description": "Filter by tier (optional)",
                        "enum": ["premium", "balanced", "fast", "economy"],
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    start = time.time()

    try:
        if name == "estimate_cost":
            model = arguments.get("model", "claude-4-sonnet")

            # Handle alternative input formats
            input_tokens = arguments.get("input_tokens", 0)
            output_tokens = arguments.get("output_tokens", 0)

            if not input_tokens and "input_text_chars" in arguments:
                input_tokens = int(arguments["input_text_chars"] / CHARS_PER_TOKEN)
            if not input_tokens and "input_text_words" in arguments:
                input_tokens = int(arguments["input_text_words"] / WORDS_PER_TOKEN)

            if not input_tokens:
                input_tokens = 1000  # default
            if not output_tokens:
                output_tokens = 500  # default

            requests_count = arguments.get("requests", 1)
            result = estimate_cost(model, input_tokens, output_tokens, requests_count)

            elapsed = round((time.time() - start) * 1000, 1)
            result["calculation_ms"] = elapsed
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "compare_models":
            input_tokens = arguments["input_tokens"]
            output_tokens = arguments["output_tokens"]
            requests_count = arguments.get("requests", 1)
            tier = arguments.get("tier")

            comparisons = compare_models(input_tokens, output_tokens, requests_count, tier)

            elapsed = round((time.time() - start) * 1000, 1)
            result = {
                "workload": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "requests": requests_count,
                    "total_tokens": (input_tokens + output_tokens) * requests_count,
                },
                "models_compared": len(comparisons),
                "cheapest": comparisons[0] if comparisons else None,
                "most_expensive": comparisons[-1] if comparisons else None,
                "all_models": comparisons,
                "calculation_ms": elapsed,
                "recommendation": _get_recommendation(comparisons, input_tokens),
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "budget_check":
            model = arguments["model"]
            input_tokens = arguments["input_tokens"]
            output_tokens = arguments["output_tokens"]
            rpd = arguments["requests_per_day"]
            budget = arguments["monthly_budget_usd"]

            # Calculate monthly cost
            monthly_requests = rpd * 30
            est = estimate_cost(model, input_tokens, output_tokens, monthly_requests)
            monthly_cost = est["cost_usd"]

            within_budget = monthly_cost <= budget
            utilization = (monthly_cost / budget * 100) if budget > 0 else 0

            # Find cheaper alternatives if over budget
            alternatives = []
            if not within_budget:
                all_models = compare_models(input_tokens, output_tokens, monthly_requests)
                alternatives = [
                    m for m in all_models
                    if m["total_cost_usd"] <= budget and m["fits"]
                ][:3]

            elapsed = round((time.time() - start) * 1000, 1)
            result = {
                "model": est["model"],
                "workload": {
                    "requests_per_day": rpd,
                    "monthly_requests": monthly_requests,
                    "input_tokens_per_request": input_tokens,
                    "output_tokens_per_request": output_tokens,
                },
                "monthly_cost_usd": round(monthly_cost, 2),
                "monthly_budget_usd": budget,
                "within_budget": within_budget,
                "budget_utilization_pct": round(utilization, 1),
                "verdict": (
                    f"✅ Within budget — ${monthly_cost:.2f}/mo ({utilization:.0f}% of ${budget:.2f} budget)"
                    if within_budget else
                    f"❌ Over budget — ${monthly_cost:.2f}/mo exceeds ${budget:.2f} budget by ${monthly_cost - budget:.2f}"
                ),
                "cheaper_alternatives": alternatives if alternatives else None,
                "daily_cost_usd": round(monthly_cost / 30, 4),
                "cost_per_request_usd": round(est["cost_usd"] / monthly_requests, 6) if monthly_requests else 0,
                "calculation_ms": elapsed,
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "list_models":
            tier = arguments.get("tier")
            models = []
            for key, p in MODEL_PRICING.items():
                if tier and p["tier"] != tier:
                    continue
                models.append({
                    "key": key,
                    "display_name": p["display_name"],
                    "model_id": p["model_id"],
                    "tier": p["tier"],
                    "input_per_1k_tokens": f"${p['input_per_1k']:.5f}",
                    "output_per_1k_tokens": f"${p['output_per_1k']:.5f}",
                    "context_window": f"{p['context_window']:,}",
                    "max_output": f"{p['max_output']:,}",
                    "best_for": p["best_for"],
                })

            elapsed = round((time.time() - start) * 1000, 1)
            result = {
                "models": models,
                "total": len(models),
                "filter": {"tier": tier} if tier else None,
                "aliases": MODEL_ALIASES,
                "calculation_ms": elapsed,
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({
            "error": str(e),
            "tool": name,
            "status": "failed",
            "duration_ms": round((time.time() - start) * 1000, 1),
        }, indent=2))]


def _get_recommendation(comparisons: list[dict], input_tokens: int) -> str:
    """Generate a model recommendation based on the comparison."""
    if not comparisons:
        return "No models available."

    fits = [m for m in comparisons if m["fits"]]
    if not fits:
        return f"⚠️ Input ({input_tokens:,} tokens) exceeds all context windows. Split into chunks."

    cheapest = fits[0]
    balanced = next((m for m in fits if m["tier"] == "balanced"), None)
    premium = next((m for m in fits if m["tier"] == "premium"), None)

    parts = [f"💰 Cheapest: {cheapest['model']} at {cheapest['total_cost_display']}"]
    if balanced and balanced != cheapest:
        parts.append(f"⚖️ Best balance: {balanced['model']} at {balanced['total_cost_display']}")
    if premium:
        parts.append(f"🏆 Highest quality: {premium['model']} at {premium['total_cost_display']}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
