"""Operator MCP server for the garage audio stack (observe, recover, control, tune).

Separate from the dashboard adapter (``garage_audio.mcp_server``/``remote_mcp``).
Core modules here are stdlib-only and importable without the MCP SDK; only
``transport`` imports the SDK. Read-only by default: every mutating tool is
refused server-side unless the operator enables writes in configuration.
"""

VERSION = '0.1.0'
SERVER_NAME = 'garage-audio-ops'
