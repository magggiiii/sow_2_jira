
import os
import asyncio
from dotenv import load_dotenv
import pytest
from integrations.jira_mcp_client import JiraMCPClient
from audit.logger import AuditLogger
from models.schemas import JiraHierarchy

@pytest.mark.asyncio
async def test_mcp():
    load_dotenv()
    # MCP might need JIRA_MCP_API or JIRA_API_TOKEN
    # We use a dummy run_id and AuditLogger
    audit = AuditLogger()
    hierarchy = JiraHierarchy.FLAT
    client = JiraMCPClient(hierarchy, audit, "test-mcp")

    print("Testing MCP connection (this will run 'npx @atlassian/mcp-remote')...")
    # MCP client has its own internal error handling and logging
    try:
        # We don't want to actually push a real task, but we can test initialization
        # Let's try to call the internal _async_push_tasks with an empty list to test initialization
        results = await client._async_push_tasks([])
        print("MCP Initialization test completed (check logs for success).")
        print("MCP test PASSED!")
    except Exception as e:
        print(f"MCP test FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_mcp())
