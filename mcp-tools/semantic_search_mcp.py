#!/usr/bin/env python3
"""
Semantic Search MCP Server — Query Expansion + Internal Doc Search
==================================================================

MCP tool server for Claude Code that provides intelligent document search
with automatic query expansion. Developers ask Claude "find docs about
authentication" and this tool expands to auth, OAuth, SSO, SAML, JWT
before searching across internal docs and wikis.

OTEL Stream 2 Telemetry (auto-captured on every invocation):
─────────────────────────────────────────────────────────────
  • tool_name: "semantic_search" | "expand_query" | "index_documents"
  • duration_ms: end-to-end latency of the search operation
  • status: pass/fail (success vs exception)
  • Bedrock request metadata: model_id, input/output tokens
  • These events flow → CloudWatch → QuickSight ROI Dashboard

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
import os
import re
import math
import hashlib
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ---------------------------------------------------------------------------
# Query Expansion Engine
# ---------------------------------------------------------------------------

# Domain-aware synonym/concept graph for enterprise dev terms
EXPANSION_MAP: dict[str, list[str]] = {
    # Authentication & Identity
    "authentication": ["auth", "authn", "login", "sign-in", "OAuth", "OAuth2", "OIDC",
                       "SSO", "SAML", "JWT", "token", "session", "credentials", "MFA", "2FA"],
    "authorization": ["authz", "RBAC", "ABAC", "permissions", "ACL", "IAM", "policy",
                      "role", "scope", "privilege", "entitlement"],
    "oauth": ["OAuth2", "OIDC", "OpenID Connect", "authorization code", "client credentials",
              "refresh token", "access token", "bearer token"],
    "sso": ["single sign-on", "SAML", "federation", "identity provider", "IdP", "SP"],
    "jwt": ["JSON Web Token", "JWS", "JWE", "claims", "bearer token"],

    # API & Networking
    "api": ["REST", "GraphQL", "gRPC", "endpoint", "route", "handler", "controller",
            "OpenAPI", "Swagger", "API Gateway"],
    "rest": ["RESTful", "HTTP API", "resource", "CRUD", "endpoint", "status code"],
    "database": ["DB", "SQL", "NoSQL", "DynamoDB", "RDS", "PostgreSQL", "MySQL",
                 "MongoDB", "Redis", "Elasticsearch", "query", "migration", "schema"],
    "cache": ["caching", "Redis", "Memcached", "CDN", "invalidation", "TTL", "eviction"],

    # AWS Services
    "lambda": ["serverless", "function", "Lambda function", "invocation", "cold start",
               "handler", "event-driven", "FaaS"],
    "s3": ["S3 bucket", "object storage", "presigned URL", "multipart upload", "lifecycle"],
    "bedrock": ["foundation model", "Claude", "Titan", "LLM", "inference", "prompt",
                "model invocation", "converse API", "InvokeModel"],
    "dynamodb": ["DDB", "NoSQL", "partition key", "sort key", "GSI", "LSI", "stream"],
    "cloudformation": ["CFN", "IaC", "infrastructure as code", "stack", "template",
                       "CDK", "SAM"],

    # CI/CD & DevOps
    "cicd": ["CI/CD", "pipeline", "continuous integration", "continuous deployment",
             "build", "deploy", "release", "GitLab CI", "GitHub Actions", "CodePipeline"],
    "deployment": ["deploy", "release", "rollback", "blue-green", "canary", "rolling update"],
    "testing": ["test", "unit test", "integration test", "e2e", "pytest", "coverage",
                "TDD", "mock", "fixture", "assertion"],
    "docker": ["container", "Dockerfile", "image", "ECS", "ECR", "Fargate", "pod"],
    "kubernetes": ["k8s", "EKS", "pod", "deployment", "service", "ingress", "helm"],

    # Security
    "security": ["vulnerability", "CVE", "OWASP", "encryption", "TLS", "SSL",
                 "secret", "credential", "compliance", "audit", "penetration test"],
    "encryption": ["TLS", "SSL", "AES", "RSA", "KMS", "at-rest", "in-transit",
                   "certificate", "key management"],

    # Observability
    "monitoring": ["observability", "metrics", "logging", "tracing", "CloudWatch",
                   "X-Ray", "OTEL", "OpenTelemetry", "alarm", "dashboard"],
    "logging": ["log", "CloudWatch Logs", "structured logging", "log level",
                "log aggregation", "ELK", "Splunk"],
}

def expand_query(query: str, max_expansions: int = 15) -> dict:
    """Expand a search query with synonyms, acronyms, and related terms."""
    query_lower = query.lower()
    tokens = re.findall(r'\b\w+\b', query_lower)

    expansions = set()
    matched_concepts = []

    for token in tokens:
        for concept, synonyms in EXPANSION_MAP.items():
            # Match if token IS the concept or appears in its synonyms
            synonym_lower = [s.lower() for s in synonyms]
            if token == concept or token in synonym_lower:
                matched_concepts.append(concept)
                expansions.update(synonyms[:max_expansions])

    # Also check multi-word matches against the original query
    for concept, synonyms in EXPANSION_MAP.items():
        if concept in query_lower:
            matched_concepts.append(concept)
            expansions.update(synonyms[:max_expansions])

    # Remove the original tokens from expansions (keep only new terms)
    expansions -= set(tokens)

    return {
        "original_query": query,
        "matched_concepts": list(set(matched_concepts)),
        "expanded_terms": sorted(list(expansions))[:max_expansions],
        "expanded_query": f"{query} {' '.join(sorted(list(expansions))[:8])}",
    }


# ---------------------------------------------------------------------------
# Simple TF-IDF Vector Store (no external deps needed)
# ---------------------------------------------------------------------------

@dataclass
class Document:
    id: str
    title: str
    content: str
    path: str
    tags: list[str] = field(default_factory=list)
    score: float = 0.0


class SimpleVectorStore:
    """Lightweight TF-IDF search engine — no numpy/sklearn needed."""

    def __init__(self):
        self.documents: list[Document] = []
        self.idf: dict[str, float] = {}
        self.doc_vectors: list[dict[str, float]] = []

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{1,}\b', text.lower())

    def _compute_tf(self, tokens: list[str]) -> dict[str, float]:
        freq = defaultdict(int)
        for t in tokens:
            freq[t] += 1
        total = len(tokens) or 1
        return {t: c / total for t, c in freq.items()}

    def index(self, documents: list[Document]):
        self.documents = documents
        N = len(documents)
        df = defaultdict(int)

        all_tokens = []
        for doc in documents:
            tokens = self._tokenize(f"{doc.title} {doc.content} {' '.join(doc.tags)}")
            all_tokens.append(tokens)
            for t in set(tokens):
                df[t] += 1

        self.idf = {t: math.log((N + 1) / (c + 1)) + 1 for t, c in df.items()}

        self.doc_vectors = []
        for tokens in all_tokens:
            tf = self._compute_tf(tokens)
            vec = {t: tf[t] * self.idf.get(t, 1.0) for t in tf}
            self.doc_vectors.append(vec)

    def search(self, query: str, top_k: int = 5) -> list[Document]:
        query_tokens = self._tokenize(query)
        query_tf = self._compute_tf(query_tokens)
        query_vec = {t: query_tf[t] * self.idf.get(t, 1.0) for t in query_tf}

        results = []
        for i, doc_vec in enumerate(self.doc_vectors):
            # Cosine similarity
            dot = sum(query_vec.get(t, 0) * doc_vec.get(t, 0) for t in query_vec)
            mag_q = math.sqrt(sum(v ** 2 for v in query_vec.values())) or 1
            mag_d = math.sqrt(sum(v ** 2 for v in doc_vec.values())) or 1
            score = dot / (mag_q * mag_d)

            if score > 0.01:
                doc = Document(
                    id=self.documents[i].id,
                    title=self.documents[i].title,
                    content=self.documents[i].content[:300] + "...",
                    path=self.documents[i].path,
                    tags=self.documents[i].tags,
                    score=round(score, 4),
                )
                results.append(doc)

        results.sort(key=lambda d: d.score, reverse=True)
        return results[:top_k]


# ---------------------------------------------------------------------------
# Sample Enterprise Doc Corpus (simulates internal wiki/docs)
# ---------------------------------------------------------------------------

SAMPLE_DOCS = [
    Document(id="DOC-001", title="Authentication Service Architecture",
             content="Our authentication service uses OAuth 2.0 with PKCE flow for SPAs and authorization code flow for server-side applications. The IdP supports SAML 2.0 federation with corporate Active Directory. JWT tokens are issued with RS256 signing. Refresh tokens have a 30-day TTL with sliding window. MFA is enforced for all admin-level operations via TOTP or WebAuthn. Session management uses Redis-backed stores with 15-minute idle timeout.",
             path="/docs/architecture/auth-service.md", tags=["auth", "OAuth", "JWT", "SSO", "SAML"]),
    Document(id="DOC-002", title="API Gateway Configuration Guide",
             content="API Gateway routes are defined in OpenAPI 3.0 specs. Rate limiting is configured per-endpoint with token bucket algorithm (100 req/s default, 1000 burst). Authentication uses Lambda authorizers that validate JWT tokens from our auth service. CORS is configured for allowed origins. Request/response transformation uses VTL templates. Caching is enabled for GET endpoints with 60-second TTL.",
             path="/docs/guides/api-gateway.md", tags=["API", "gateway", "rate-limit", "REST"]),
    Document(id="DOC-003", title="DynamoDB Data Modeling Best Practices",
             content="Single-table design is our standard pattern. Use composite sort keys for query flexibility (SK: TYPE#TIMESTAMP). GSIs should be sparse — only project attributes you query. Enable point-in-time recovery for all production tables. Use DynamoDB Streams for CDC to Elasticsearch. Provisioned capacity with auto-scaling for predictable workloads; on-demand for spiky patterns. Avoid scan operations — always use query with partition key.",
             path="/docs/best-practices/dynamodb.md", tags=["DynamoDB", "database", "NoSQL"]),
    Document(id="DOC-004", title="CI/CD Pipeline Standards",
             content="All repos must use the standard GitLab CI template. Pipeline stages: lint → unit-test → build → integration-test → security-scan → deploy-staging → approval → deploy-prod. Docker images are built with multi-stage Dockerfiles and pushed to ECR. Deployment uses CodeDeploy with blue-green strategy. Rollback is automatic if health checks fail within 5 minutes. Test coverage must be ≥80% for merge approval.",
             path="/docs/standards/cicd-pipeline.md", tags=["CI/CD", "pipeline", "GitLab", "deployment"]),
    Document(id="DOC-005", title="Security Scanning and Compliance",
             content="SAST scanning runs on every MR using Semgrep with our custom rules. DAST runs nightly against staging. Dependency scanning uses Snyk for CVE detection with auto-PR for patches. Container image scanning uses Trivy. All findings above 'medium' severity block deployment. SOC2 compliance requires quarterly access reviews, encryption at rest (KMS), and audit logging to CloudTrail. Secrets must use AWS Secrets Manager — never environment variables.",
             path="/docs/security/scanning-compliance.md", tags=["security", "SAST", "DAST", "compliance"]),
    Document(id="DOC-006", title="Observability and Monitoring Setup",
             content="All services emit structured JSON logs to CloudWatch Logs. Metrics are published via CloudWatch EMF (Embedded Metric Format). Distributed tracing uses X-Ray with OTEL SDK instrumentation. Custom dashboards in CloudWatch show p50/p95/p99 latencies, error rates, and throughput. Alarms trigger SNS → PagerDuty for on-call. SLOs are defined as 99.9% availability and p99 < 500ms for critical APIs.",
             path="/docs/operations/observability.md", tags=["monitoring", "logging", "tracing", "OTEL"]),
    Document(id="DOC-007", title="Lambda Best Practices",
             content="Use ARM64 (Graviton) for 20% cost reduction. Keep handlers thin — business logic in separate modules for testability. Use Lambda Powertools for structured logging, tracing, and metrics. Cold start mitigation: keep deployment packages small (<50MB), use provisioned concurrency for latency-sensitive functions. Environment variables for configuration, Secrets Manager for sensitive values. Use Lambda Destinations for async error handling instead of DLQ.",
             path="/docs/best-practices/lambda.md", tags=["Lambda", "serverless", "best-practices"]),
    Document(id="DOC-008", title="Bedrock Integration Guide",
             content="Use the Converse API for multi-turn conversations — it handles message formatting across models. Supported models: Claude 3.5 Sonnet, Claude 3 Haiku, Claude 3 Opus, Titan Text. Enable model invocation logging to S3 for audit. Use Guardrails for content filtering. Implement exponential backoff for throttling (429). Cost optimization: use Haiku for simple tasks, Sonnet for complex reasoning, Opus for critical analysis. Cross-region inference for availability.",
             path="/docs/guides/bedrock-integration.md", tags=["Bedrock", "Claude", "LLM", "AI"]),
    Document(id="DOC-009", title="Infrastructure as Code with CDK",
             content="All infrastructure is defined in AWS CDK (TypeScript). Use L2 constructs where available. Custom constructs for company patterns (VPC, ECS service, Lambda function). Stack naming convention: {team}-{service}-{env}. Use cdk-nag for compliance validation. CDK Pipelines for self-mutating deployment pipelines. Secrets and config are in SSM Parameter Store with environment-specific paths. Cross-stack references use SSM lookups, not Fn::ImportValue.",
             path="/docs/infrastructure/cdk-guide.md", tags=["CDK", "IaC", "CloudFormation", "infrastructure"]),
    Document(id="DOC-010", title="Kubernetes (EKS) Operations Runbook",
             content="EKS clusters use managed node groups with Bottlerocket AMI. Pod autoscaling via HPA based on CPU/memory and custom CloudWatch metrics. Cluster autoscaler for node scaling. Service mesh with App Mesh for mTLS and traffic management. Helm charts stored in ECR. GitOps with ArgoCD for deployment reconciliation. Namespace isolation per team with ResourceQuotas and NetworkPolicies. Secrets managed by External Secrets Operator from Secrets Manager.",
             path="/docs/operations/eks-runbook.md", tags=["Kubernetes", "EKS", "container", "operations"]),
    Document(id="DOC-011", title="Error Handling and Retry Patterns",
             content="Use exponential backoff with jitter for all external service calls. Circuit breaker pattern for cascading failure prevention (use resilience4j or custom implementation). Retry budget: max 3 retries with 100ms/200ms/400ms delays. Dead letter queues for async processing failures. Idempotency keys for all mutating API operations. Structured error responses follow RFC 7807 (Problem Details). Log correlation IDs across all service boundaries.",
             path="/docs/patterns/error-handling.md", tags=["error-handling", "retry", "resilience", "patterns"]),
    Document(id="DOC-012", title="Data Encryption Standards",
             content="All data at rest uses AES-256 via AWS KMS customer-managed keys. Key rotation is automatic (annual). TLS 1.2+ enforced for all data in transit. S3 bucket policies deny unencrypted uploads (aws:SecureTransport). RDS uses encrypted storage and SSL connections. DynamoDB encryption uses AWS-owned keys for non-sensitive, CMK for PII. Field-level encryption for PII in application layer before storage. Key hierarchy: root key → data key → envelope encryption.",
             path="/docs/security/encryption-standards.md", tags=["encryption", "KMS", "TLS", "security"]),
]

# ---------------------------------------------------------------------------
# Initialize global store
# ---------------------------------------------------------------------------
STORE = SimpleVectorStore()
STORE.index(SAMPLE_DOCS)

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
server = Server("semantic-search-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="semantic_search",
            description=(
                "Search internal documentation and wiki with intelligent query expansion. "
                "Automatically expands queries with synonyms, acronyms, and related terms "
                "(e.g., 'authentication' → OAuth, SSO, SAML, JWT). Returns ranked results "
                "with relevance scores. Use for finding architecture docs, best practices, "
                "runbooks, and guides."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — can be natural language or keywords (e.g., 'how does authentication work', 'DynamoDB best practices')"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5, max: 20)",
                        "default": 5,
                    },
                    "expand": {
                        "type": "boolean",
                        "description": "Enable query expansion with synonyms and related terms (default: true)",
                        "default": True,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="expand_query",
            description=(
                "Expand a search query with synonyms, acronyms, and related technical terms. "
                "Useful for understanding the search space before running a search, or for "
                "building more comprehensive queries. Returns matched concepts and expanded terms."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Query to expand"
                    },
                    "max_expansions": {
                        "type": "integer",
                        "description": "Maximum number of expansion terms (default: 15)",
                        "default": 15,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="index_documents",
            description=(
                "Index a directory of documents for searching. Scans .md, .txt, .py, .json, "
                ".yaml files and adds them to the search index. Use to make project docs "
                "searchable. Returns count of indexed documents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Path to directory containing documents to index"
                    },
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File extensions to index (default: ['.md', '.txt', '.py', '.json', '.yaml'])",
                        "default": [".md", ".txt", ".py", ".json", ".yaml"],
                    },
                },
                "required": ["directory"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    start = time.time()

    try:
        if name == "semantic_search":
            query = arguments["query"]
            top_k = min(arguments.get("top_k", 5), 20)
            do_expand = arguments.get("expand", True)

            # Step 1: Optional query expansion
            expansion = expand_query(query) if do_expand else None
            search_query = expansion["expanded_query"] if expansion else query

            # Step 2: Search
            results = STORE.search(search_query, top_k=top_k)

            # Step 3: Format response
            elapsed = round((time.time() - start) * 1000, 1)

            output = {
                "query": query,
                "search_query_used": search_query,
                "expansion": expansion,
                "results": [
                    {
                        "rank": i + 1,
                        "id": r.id,
                        "title": r.title,
                        "path": r.path,
                        "tags": r.tags,
                        "relevance_score": r.score,
                        "snippet": r.content,
                    }
                    for i, r in enumerate(results)
                ],
                "total_results": len(results),
                "search_duration_ms": elapsed,
                "index_size": len(STORE.documents),
            }

            return [TextContent(type="text", text=json.dumps(output, indent=2))]

        elif name == "expand_query":
            query = arguments["query"]
            max_exp = arguments.get("max_expansions", 15)
            result = expand_query(query, max_exp)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "index_documents":
            directory = Path(arguments["directory"]).expanduser()
            extensions = arguments.get("extensions", [".md", ".txt", ".py", ".json", ".yaml"])

            if not directory.exists():
                return [TextContent(type="text", text=json.dumps({
                    "error": f"Directory not found: {directory}",
                    "status": "failed"
                }))]

            new_docs = []
            for ext in extensions:
                for fpath in directory.rglob(f"*{ext}"):
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="replace")[:5000]
                        doc_id = hashlib.md5(str(fpath).encode()).hexdigest()[:8]
                        new_docs.append(Document(
                            id=f"IDX-{doc_id}",
                            title=fpath.stem.replace("_", " ").replace("-", " ").title(),
                            content=content,
                            path=str(fpath),
                            tags=[ext.lstrip("."), fpath.parent.name],
                        ))
                    except Exception:
                        pass

            # Re-index with combined docs
            all_docs = SAMPLE_DOCS + new_docs
            STORE.index(all_docs)

            elapsed = round((time.time() - start) * 1000, 1)
            return [TextContent(type="text", text=json.dumps({
                "status": "indexed",
                "new_documents": len(new_docs),
                "total_index_size": len(all_docs),
                "directory": str(directory),
                "extensions_scanned": extensions,
                "duration_ms": elapsed,
            }, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({
            "error": str(e),
            "tool": name,
            "status": "failed",
            "duration_ms": round((time.time() - start) * 1000, 1),
        }))]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
