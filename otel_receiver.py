#!/usr/bin/env python3
import subprocess, sys

# Auto-install deps
try:
    import flask
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "flask", "-q"], check=True)
    import flask

from flask import Flask, request
import json, datetime
import os

app = Flask(__name__)
app.logger.disabled = True
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # suppress request noise

def ts():
    return datetime.datetime.now().strftime('%H:%M:%S')

def parse_attrs(attrs):
    out = {}
    for a in attrs:
        v = a.get('value', {})
        out[a['key']] = (v.get('stringValue') or v.get('intValue') or
                         v.get('doubleValue') or v.get('boolValue') or '')
    return out

def try_parse(req):
    """Try JSON first, then show raw bytes info as fallback."""
    data = req.get_json(force=True, silent=True)
    if data:
        return data, None
    raw = req.data
    ct = req.content_type
    return None, f"binary/protobuf ({len(raw)} bytes, content-type: {ct}) — add OTEL_EXPORTER_OTLP_PROTOCOL=http/json to get parsed output"

@app.route('/v1/metrics', methods=['POST'])
def metrics():
    data, raw_msg = try_parse(request)
    if not data:
        print(f"\n[{ts()}] 📊 METRICS received ({raw_msg})")
        return '', 200
    found = []
    for rm in data.get('resourceMetrics', []):
        for sm in rm.get('scopeMetrics', []):
            for m in sm.get('metrics', []):
                name = m.get('name', '?')
                for dp in m.get('sum', {}).get('dataPoints', []):
                    val = dp.get('asDouble', dp.get('asInt', 0))
                    attrs = parse_attrs(dp.get('attributes', []))
                    found.append((name, val, attrs))
                for dp in m.get('gauge', {}).get('dataPoints', []):
                    val = dp.get('asDouble', dp.get('asInt', 0))
                    attrs = parse_attrs(dp.get('attributes', []))
                    found.append((name, val, attrs))
    if found:
        print(f"\n{'─'*60}")
        print(f"[{ts()}] 📊  METRICS  ({len(found)} data points)")
        for name, val, attrs in found:
            attr_str = '  '.join(f'{k}={v}' for k,v in attrs.items() if v)
            print(f"  {name:45s} = {val}")
            if attr_str:
                print(f"  {'':45s}   ↳ {attr_str}")
    return '', 200

@app.route('/v1/logs', methods=['POST'])
def logs():
    data, raw_msg = try_parse(request)
    if not data:
        print(f"\n[{ts()}] 📝 LOGS received ({raw_msg})")
        return '', 200
    records = []
    for rl in data.get('resourceLogs', []):
        for sl in rl.get('scopeLogs', []):
            for r in sl.get('logRecords', []):
                body = r.get('body', {}).get('stringValue', '')
                attrs = parse_attrs(r.get('attributes', []))
                records.append((body, attrs))
    if records:
        print(f"\n{'─'*60}")
        print(f"[{ts()}] 📝  LOG EVENTS  ({len(records)} records)")
        for body, attrs in records:
            print(f"  event: {body[:80]}")
            for k, v in attrs.items():
                if v:
                    print(f"    {k}: {str(v)[:80]}")
    return '', 200

@app.route('/v1/traces', methods=['POST'])
def traces():
    data, raw_msg = try_parse(request)
    if not data:
        print(f"\n[{ts()}] 🔍 TRACES received ({raw_msg})")
        return '', 200
    data = request.get_json(force=True, silent=True) or {}
    spans = []
    for rt in data.get('resourceSpans', []):
        for st in rt.get('scopeSpans', []):
            for s in st.get('spans', []):
                spans.append(s.get('name', '?'))
    if spans:
        print(f"\n{'─'*60}")
        print(f"[{ts()}] 🔍  TRACES  spans: {', '.join(spans[:10])}")
    return '', 200

if __name__ == '__main__':
    print("="*60)
    print("  Claude Code OTEL Receiver")
    print("  Listening on http://localhost:4318")
    print("="*60)
    print("\n  ── Terminal 2: run these BEFORE starting claude ──")
    print("  export CLAUDE_CODE_ENABLE_TELEMETRY=1")
    print("  export OTEL_METRICS_EXPORTER=otlp")
    print("  export OTEL_LOGS_EXPORTER=otlp")
    print("  export OTEL_EXPORTER_OTLP_PROTOCOL=http/json   ← REQUIRED for human-readable output")
    print("  export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318")
    print("  claude")
    print("\n  ── Type /exit in Claude Code to flush metrics ──\n")
    print("\n  Waiting for data...\n")
    app.run(host='0.0.0.0', port=4318, debug=False, use_reloader=False)
