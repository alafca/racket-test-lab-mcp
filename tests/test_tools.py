import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from racket_lab import server


def test_registers_tools_resource_and_prompt():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "list_rackets", "get_racket", "get_test_runs", "compare_rackets", "find_failures",
    }
    assert all(t.annotations.readOnlyHint for t in tools)
    assert all(t.description for t in tools)
    resources = asyncio.run(server.mcp.list_resources())
    assert [str(r.uri) for r in resources] == ["lab://schema"]
    prompts = asyncio.run(server.mcp.list_prompts())
    assert [p.name for p in prompts] == ["weekly_rd_summary"]


def test_tool_call_through_mcp(db_path):
    [content] = asyncio.run(server.mcp.call_tool("get_racket", {"racket_id": "RK-100-A"}))
    assert json.loads(content.text)["racket"]["id"] == "RK-100-A"


def test_invalid_input_is_tool_error(db_path):
    with pytest.raises(ToolError, match="Invalid metric"):
        server.compare_rackets(racket_ids=["RK-100-A"], metric="price")
    with pytest.raises(ToolError, match="Unknown racket"):
        server.get_racket(racket_id="ZZ-99-Z")
    with pytest.raises(ToolError, match="Invalid racket_id"):
        server.get_test_runs(racket_id="RK-100-A' OR 1=1--")


def test_invalid_input_sets_is_error_over_mcp(db_path):
    async def call():
        from mcp.shared.memory import create_connected_server_and_client_session as connect
        async with connect(server.mcp._mcp_server) as client:
            return await client.call_tool("compare_rackets", {"racket_ids": ["RK-100-A"], "metric": "price"})
    result = asyncio.run(call())
    assert result.isError and "Invalid metric" in result.content[0].text
    assert "Traceback" not in result.content[0].text


@pytest.mark.parametrize("value", ["missing.db", "", "."])
def test_bad_db_path_is_tool_error(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)  # "" falls back to data/lab.db, which doesn't exist here
    monkeypatch.setenv("RACKET_LAB_DB", value)
    with pytest.raises(ToolError, match="Database not found"):
        server.list_rackets()


def test_tool_calls_are_logged(db_path, caplog):
    with caplog.at_level("INFO", logger="racket_lab"):
        server.find_failures(since="2026-01-01")
    assert "tool=find_failures" in caplog.text and "duration_ms=" in caplog.text


def test_weekly_prompt_mentions_tools():
    text = server.weekly_rd_summary()
    assert "find_failures" in text and "since" in text
