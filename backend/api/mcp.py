"""Minimal MCP (Model Context Protocol) server over JSON-RPC 2.0.

Implements the Streamable HTTP transport in stateless mode: a single POST
/mcp endpoint handles initialize, tools/list and tools/call. Exposes the
DocuMind corpus to MCP clients (Claude Desktop, MCP Inspector, and similar
tooling) with three tools.
"""

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "documind-rag", "version": "1.0.0", "title": "DocuMind AI document Q&A"}

import rag
import store

TOOLS = [
    {
        "name": "search_documents",
        "description": ("Semantic search across the indexed PDF corpus. Returns the most "
                        "relevant passages with document and chunk identifiers."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "topK": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_documind",
        "description": ("Ask a question and get a cited answer synthesized from the indexed "
                        "documents. Served through the two-tier response cache."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "topK": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
            },
            "required": ["question"],
        },
    },
    {
        "name": "list_documents",
        "description": "List recently uploaded documents and their processing status.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
        },
    },
]


def _text(s: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": is_error}


def _call_tool(name: str, args: dict) -> dict:
    try:
        if name == "search_documents":
            query = str(args.get("query", "")).strip()
            if not query:
                return _text("query is required", True)
            chunks, _, _ = rag.retrieve(query, int(args.get("topK", 4)))
            if not chunks:
                return _text("No matching passages found.")
            lines = [
                f"[{i + 1}] {c['filename']} (chunk {c['chunkIdx']}, score {c['score']}):\n{c['text']}"
                for i, c in enumerate(chunks)
            ]
            return _text("\n\n".join(lines))
        if name == "ask_documind":
            question = str(args.get("question", "")).strip()
            if not question:
                return _text("question is required", True)
            res, tier = rag.answer_question(question, int(args.get("topK", 4)))
            sources = "\n".join(
                f"[{i + 1}] {c['filename']} (chunk {c['chunkIdx']}, score {c['score']})"
                for i, c in enumerate(res.get("sources", []))
            )
            text = res.get("answer", "")
            if sources:
                text += f"\n\nSources:\n{sources}"
            text += f"\n\n(cache: {tier})"
            return _text(text)
        if name == "list_documents":
            items = store.list_jobs(int(args.get("limit", 10)))
            if not items:
                return _text("No documents uploaded yet.")
            lines = [
                f"- {i.get('jobId')} | {i.get('filename')} | status={i.get('status')}"
                f" | chunks={i.get('chunkCount', '-')}"
                for i in items
            ]
            return _text("\n".join(lines))
        return _text(f"Unknown tool: {name}", True)
    except Exception as exc:
        return _text(f"Tool execution failed: {exc}", True)


def handle_rpc(body: dict):
    """Handle one JSON-RPC message.

    Returns (http_status, response_dict_or_None). None means "no response"
    (notifications get 202 Accepted with an empty body).
    """
    if not isinstance(body, dict):
        return 400, {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "Parse error"}}
    method = body.get("method")
    rid = body.get("id")
    if method is None:
        return 400, {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32600, "message": "Invalid Request"}}

    if rid is None:  # JSON-RPC notification (e.g. notifications/initialized)
        return 202, None

    if method == "initialize":
        return 200, {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }}
    if method == "ping":
        return 200, {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return 200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = body.get("params") or {}
        result = _call_tool(str(params.get("name", "")), params.get("arguments") or {})
        return 200, {"jsonrpc": "2.0", "id": rid, "result": result}
    return 200, {"jsonrpc": "2.0", "id": rid,
                 "error": {"code": -32601, "message": f"Method not found: {method}"}}
