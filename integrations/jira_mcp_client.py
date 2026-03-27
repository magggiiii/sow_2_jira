# integrations/jira_mcp_client.py

import os
import asyncio
import json
import re
from typing import Optional, Union
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from models.schemas import ManagedTask, JiraHierarchy, JiraPushResult, TaskFlag
from audit.logger import AuditLogger
from pipeline.observability import logger, tracer, trace_span

class JiraMCPError(Exception):
    pass

class JiraMCPClient:
    """
    A Jira client powered by the official Atlassian Rovo MCP remote server.
    Uses the official `mcp` SDK with headless CLI flags for authentication.
    """

    def __init__(self, hierarchy: JiraHierarchy, audit: AuditLogger, run_id: str):
        self.hierarchy = hierarchy
        self.audit = audit
        self.run_id = run_id
        self.project_key = os.environ["JIRA_PROJECT_KEY"]
        self.server = os.environ["JIRA_SERVER"]

        with logger.contextualize(agent="JiraClient", run_id=self.run_id):
            logger.info(f"Initializing Official Jira Rovo MCP Client for project {self.project_key}")

    @trace_span("JIRA_PUSH_ALL", agent="JiraMCPClient")
    def push_tasks(self, tasks: list[ManagedTask]) -> list[JiraPushResult]:
        """Synchronous wrapper for the async MCP push."""
        return asyncio.run(self._async_push_tasks(tasks))

    async def _async_push_tasks(self, tasks: list[ManagedTask]) -> list[JiraPushResult]:
        logger.info(f"Pushing {len(tasks)} tasks to Jira via Official Rovo MCP...")
        results = []

        email = os.environ["JIRA_EMAIL"]
        # Use JIRA_MCP_API token for official proxy auth
        token = os.environ.get("JIRA_MCP_API") or os.environ.get("JIRA_API_TOKEN")
        
        # The official proxy command
        # npx -y @atlassian/mcp-remote <remote-url> --email <email> --token <token>
        # Note: We wrap it in node to filter stdout logs just like before
        base_cmd = f"npx -y @atlassian/mcp-remote https://mcp.atlassian.com/v1/mcp --email {email} --token {token}"
        
        wrapper_cmd = (
            "node -e '"
            "const { spawn } = require(\"child_process\"); "
            f"const s = spawn(\"{base_cmd}\", [], {{ shell: true, env: process.env, stdio: [\"pipe\", \"pipe\", \"inherit\"] }}); "
            "s.stdout.on(\"data\", d => { "
            "  d.toString().split(\"\\n\").forEach(l => { "
            "    if (l.trim().startsWith(\"{\")) process.stdout.write(l + \"\\n\"); "
            "    else if (l.trim()) process.stderr.write(l + \"\\n\"); "
            "  }); "
            "}); "
            "process.stdin.on(\"data\", d => s.stdin.write(d)); "
            "s.on(\"exit\", c => process.exit(c));"
            "'"
        )

        server_params = StdioServerParameters(
            command="sh",
            args=["-c", wrapper_cmd],
            env=os.environ.copy()
        )

        try:
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    logger.success("Official MCP connection initialized")
                    
                    if self.hierarchy == JiraHierarchy.FLAT:
                        for task in tasks:
                            results.append(await self._create_task(session, task, None, "Task"))

                    elif self.hierarchy == JiraHierarchy.EPIC_TASK:
                        epic_cache = {}
                        for task in tasks:
                            section = task.source_refs[0].section_title if task.source_refs else "General"
                            if section not in epic_cache:
                                epic_cache[section] = await self._create_issue(session, f"[SOW] {section}", "Epic")
                            
                            results.append(await self._create_task(session, task, epic_cache[section], "Task"))

                    elif self.hierarchy == JiraHierarchy.STORY_SUBTASK:
                        story_cache = {}
                        for task in tasks:
                            section = task.source_refs[0].section_title if task.source_refs else "General"
                            if section not in story_cache:
                                story_key = await self._create_issue(session, f"[SOW] {section}", "Story")
                                if story_key: story_cache[section] = story_key
                            
                            results.append(await self._create_task(session, task, story_cache[section], "Sub-task"))

        except Exception as e:
            logger.error(f"Official MCP Critical Error: {e}")
            for t in tasks: results.append(JiraPushResult(task_id=t.id, success=False, error=str(e)))
            
        return results

    async def _create_issue(self, session: ClientSession, summary: str, issue_type: str) -> Optional[str]:
        """Helper to create a parent issue (Epic/Story) using official tool name."""
        logger.info(f"Creating {issue_type}: {summary[:50]}...")
        try:
            # Official Tool Name: create-issue
            res = await session.call_tool("create-issue", arguments={
                "projectKey": self.project_key,
                "summary": summary,
                "issueType": issue_type
            })
            content = res.content[0].text if res.content else ""
            import re
            match = re.search(fr"{self.project_key}-\d+", content)
            return match.group(0) if match else None
        except Exception as e:
            logger.error(f"Official MCP failed to create {issue_type}: {e}")
            return None

    async def _create_task(self, session: ClientSession, task: ManagedTask, parent_key: str | None, issue_type: str) -> JiraPushResult:
        """Helper to create a task/sub-task using official tool name."""
        desc = f"Description: {task.short_description}\n\nAcceptance Criteria:\n"
        desc += "\n".join([f"- {ac}" for ac in (task.acceptance_criteria or [])])
        
        args = {
            "projectKey": self.project_key,
            "summary": task.title[:255],
            "description": desc,
            "issueType": issue_type
        }
        # Official proxy uses parentKey argument name
        if parent_key: args["parentKey"] = parent_key

        try:
            # Official Tool Name: create-issue
            res = await session.call_tool("create-issue", arguments=args)
            content = res.content[0].text if res.content else ""
            import re
            match = re.search(fr"{self.project_key}-\d+", content)
            issue_key = match.group(0) if match else "UNKNOWN"
            
            if res.isError: raise Exception(content)

            self.audit.log(self.run_id, "JiraMCPClient", "PUSHED", f"Created {issue_key}", task_id=str(task.id))
            return JiraPushResult(task_id=task.id, success=True, jira_issue_key=issue_key, 
                                jira_issue_url=f"{self.server}/browse/{issue_key}")
        except Exception as e:
            return JiraPushResult(task_id=task.id, success=False, error=str(e))
