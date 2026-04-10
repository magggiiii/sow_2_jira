import os
import json
import time
from datetime import datetime
from langfuse import Langfuse
from pipeline.agents.extraction import TaskExtractionAgent
from pipeline.llm_client import LLMClient
from audit.logger import AuditLogger
from models.schemas import LLMMode, JiraHierarchy, ProviderConfig
from pipeline.llm_router import configure_litellm_for_mode

# Initialize Langfuse client
os.environ["LANGFUSE_PUBLIC_KEY"] = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
os.environ["LANGFUSE_SECRET_KEY"] = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
os.environ["LANGFUSE_HOST"] = os.getenv("LANGFUSE_HOST", "http://localhost:3002")

def run_eval_dataset(dry_run=False):
    langfuse = Langfuse()
    dataset_name = "sow-hierarchical-golden-set"
    run_name = f"eval-run-{int(time.time())}"
    
    print(f"Fetching dataset: {dataset_name}...")
    try:
        dataset = langfuse.get_dataset(dataset_name)
    except Exception as e:
        print(f"Error fetching dataset: {e}")
        return

    # Setup Extraction Agent
    run_id = f"eval-{int(time.time())}"
    audit = AuditLogger(run_id)
    
    # Configure for local/eval mode (Ollama via Bifrost)
    # We'll assume LLM_MODE=local and BIFROST_URL is set in environment
    llm_mode = LLMMode.LOCAL
    provider_config = configure_litellm_for_mode(llm_mode)
    
    llm_client = LLMClient(mode=llm_mode, audit_logger=audit, run_id=run_id)
    llm_client.provider_config = provider_config
    llm_client.model = provider_config.model
    
    extraction_agent = TaskExtractionAgent(
        llm_client=llm_client,
        audit_logger=audit,
        run_id=run_id,
        threshold=0.1, # Low threshold for evaluation
        max_section_chars=16000
    )

    print(f"Starting Dataset Run: {run_name}")
    
    for item in dataset.items:
        print(f"Processing Item: {item.input.get('section_title', 'Unknown')}")
        
        section_text = item.input.get("section_text", "")
        node = {
            "node_id": item.id,
            "title": item.input.get("section_title", "Eval Node"),
            "page_start": 0,
            "page_end": 0
        }

        if dry_run:
            print(f"Dry run: skipping extraction for {node['title']}")
            continue

        # Run extraction
        # Note: we use epic_task hierarchy as requested
        raw_tasks = extraction_agent.extract(
            node=node,
            section_text=section_text,
            hierarchy=JiraHierarchy.EPIC_TASK.value
        )

        # Link to Langfuse Dataset Item
        # We need to create a trace and then link it
        trace = langfuse.trace(
            name="eval-extraction",
            input=item.input,
            output=[task.model_dump() for task in raw_tasks],
            metadata={"run_id": run_id, "dataset_item_id": item.id}
        )
        
        item.link(trace.id, run_name)
        print(f"  ✓ Linked trace {trace.id} to dataset item")

    print(f"Dataset Run '{run_name}' complete!")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    run_eval_dataset(dry_run=args.dry_run)
