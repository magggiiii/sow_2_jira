# main.py

import os
import sys
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from rich import print as rprint
from rich.console import Console
from rich.prompt import Prompt, Confirm

# Load environment FIRST
load_dotenv()

from models.schemas import RunConfig, LLMMode, JiraHierarchy
from pipeline.orchestrator import PipelineOrchestrator
from audit.logger import AuditLogger

console = Console()

def parse_cli_flags() -> dict:
    return {
        "skip_indexing": "--skip-indexing" in sys.argv
    }

def ensure_ollama_model(model_name: str) -> None:
    """Check if model is available locally; pull if not."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10
        )
        if model_name not in result.stdout:
            rprint(f"[yellow]Ollama model '{model_name}' not found. Pulling now...[/yellow]")
            rprint("[dim](This is a one-time download of ~4GB for qwen2.5:7b)[/dim]")
            subprocess.run(["ollama", "pull", model_name], check=True)
            rprint(f"[green]Model '{model_name}' ready.[/green]")
        else:
            rprint(f"[green]Ollama model '{model_name}' already available.[/green]")
    except FileNotFoundError:
        raise RuntimeError(
            "Ollama not found. Install from https://ollama.com then re-run."
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to pull model '{model_name}': {e}")


def startup_wizard() -> RunConfig:
    """
    Interactive CLI wizard to collect run configuration.
    This runs before the pipeline and before the UI.
    """
    console.rule("[bold cyan]SOW-to-Jira POC[/bold cyan]")
    rprint("[cyan]Welcome! Answer a few questions to configure this run.[/cyan]\n")

    # ── 1. SOW PDF Path ───────────────────────────────────────────────────────
    while True:
        pdf_path = Prompt.ask("Path to SOW PDF", default="data/sample.pdf")
        if Path(pdf_path).exists() and pdf_path.lower().endswith(".pdf"):
            break
        rprint(f"[red]File not found or not a PDF: {pdf_path}[/red]")

    # ── 2. LLM Mode ───────────────────────────────────────────────────────────
    rprint("\nLLM Mode:")
    rprint("  [bold]1[/bold] → API (z.ai GLM via Maxim Bifrost) — Recommended")
    rprint("  [bold]2[/bold] → Local (Ollama via Maxim Bifrost) — Requires Ollama running")
    llm_choice = Prompt.ask("Choose", choices=["1", "2"], default="1")
    llm_mode = LLMMode.API if llm_choice == "1" else LLMMode.LOCAL

    # ── 3. Jira Hierarchy ─────────────────────────────────────────────────────
    rprint("\nHow should tasks be structured in Jira?")
    rprint("  [bold]1[/bold] → Flat — All items created as Tasks (simplest)")
    rprint("  [bold]2[/bold] → Epic > Task — SOW sections become Epics, items become Tasks")
    rprint("  [bold]3[/bold] → Story > Sub-task — SOW sections become Stories, items become Sub-tasks")
    hier_choice = Prompt.ask("Choose", choices=["1", "2", "3"], default="1")
    hierarchy_map = {"1": JiraHierarchy.FLAT, "2": JiraHierarchy.EPIC_TASK, "3": JiraHierarchy.STORY_SUBTASK}
    jira_hierarchy = hierarchy_map[hier_choice]

    # ── 4. Jira Project Key ───────────────────────────────────────────────────
    default_key = os.getenv("JIRA_PROJECT_KEY", "PROJ")
    jira_project_key = Prompt.ask("Jira project key", default=default_key)

    flags = parse_cli_flags()

    config = RunConfig(
        sow_pdf_path=pdf_path,
        llm_mode=llm_mode,
        jira_hierarchy=jira_hierarchy,
        jira_project_key=jira_project_key,
        skip_indexing=flags["skip_indexing"]
    )

    rprint(f"\n[bold green]Configuration locked:[/bold green]")
    rprint(f"  PDF: {config.sow_pdf_path}")
    rprint(f"  LLM: {config.llm_mode.value}")
    rprint(f"  Hierarchy: {config.jira_hierarchy.value}")
    rprint(f"  Jira Project: {config.jira_project_key}")
    rprint(f"  Skip Indexing: {config.skip_indexing}")
    rprint(f"  Run ID: {config.run_id}\n")

    if not Confirm.ask("Proceed with pipeline?", default=True):
        rprint("[yellow]Aborted.[/yellow]")
        sys.exit(0)

    return config


def main():
    # Ensure data dir exists
    Path("data").mkdir(exist_ok=True)

    # Load app config
    with open("config/sow_config.json") as f:
        app_config = json.load(f)

    # Startup wizard
    run_config = startup_wizard()

    if run_config.llm_mode == LLMMode.LOCAL:
        ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        ensure_ollama_model(ollama_model)

    # Initialize audit logger
    audit = AuditLogger()

    # Update Jira project key from user input
    os.environ["JIRA_PROJECT_KEY"] = run_config.jira_project_key

    # Run pipeline
    orchestrator = PipelineOrchestrator(run_config, app_config, audit)
    tasks = orchestrator.run()

    rprint("\n[bold cyan]Pipeline complete.[/bold cyan]")
    rprint(f"[bold]{len(tasks)}[/bold] tasks ready for review.\n")
    rprint("Launch the review UI with:")
    rprint("  [bold green]uvicorn ui.server:app --reload[/bold green]\n")


if __name__ == "__main__":
    main()