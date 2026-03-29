# pipeline/indexer.py

"""
Document indexer powered by VectifyAI PageIndex.
Builds a hierarchical tree from a PDF using LLM-driven TOC detection,
verification, and recursive node splitting.
"""

import sys
import json
from pathlib import Path
from rich import print as rprint

# Ensure pageindex is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from pageindex.page_index import page_index_main
from pageindex.utils import ConfigLoader, structure_to_list, add_node_text


class DocumentIndexer:
    """
    Wraps PageIndex's page_index_main() to produce:
    - A hierarchical tree (self.last_tree) for UI grouping
    - A flat node list for the extraction pipeline
    """

    def __init__(self, config: dict, model: str):
        """
        Args:
            config: Pipeline config dict (from sow_config.json)
            model:  litellm-compatible model string (e.g. "glm-4", "ollama/qwen2.5:7b")
        """
        self.model = model
        self.config = config
        self.last_tree = None
        self.last_result = None

    def build_tree(self, pdf_path: str, status_callback=None, stop_event=None) -> list[dict]:
        """
        Run PageIndex on the PDF file.
        Returns a flat list of nodes for the extraction pipeline.
        Stores the full tree in self.last_tree for hierarchical access.
        """
        def _report(msg, progress=0.05):
            if status_callback:
                status_callback(1, msg, progress)
            rprint(f"[cyan]{msg}[/cyan]")

        _report(f"PageIndex: Parsing PDF {Path(pdf_path).name}...", 0.05)

        opt = ConfigLoader(
            default_path=str(Path(__file__).parent.parent / "pageindex" / "config.yaml")
        ).load({
            "model": self.model,
            "max_page_num_each_node": self.config["pipeline"].get("pageindex_max_pages_per_node", 10),
            "max_token_num_each_node": self.config["pipeline"].get("pageindex_max_tokens_per_node", 20000),
            "if_add_node_id": "yes",
            "if_add_node_summary": "yes",
            "if_add_node_text": "yes",
        })

        def pageindex_cb(msg):
            _report(msg)

        result = page_index_main(pdf_path, opt, status_callback=pageindex_cb, stop_event=stop_event)
        
        if stop_event and stop_event.is_set():
            _report("PageIndex: Extraction aborted by user", 0.30)
            return []

        _report("PageIndex: Structuring document tree...", 0.20)
        self.last_result = result
        self.last_tree = result.get("structure", [])

        flat_nodes = self.flatten_tree(self.last_tree)
        _report(f"PageIndex complete: {len(flat_nodes)} nodes extracted", 0.30)
        return flat_nodes

    def flatten_tree(self, tree) -> list[dict]:
        """
        Convert PageIndex's hierarchical tree to the pipeline's flat node format.
        Each node has: node_id, title, page_start, page_end, summary, text
        """
        raw_nodes = structure_to_list(tree) if tree else []
        nodes = []
        for n in raw_nodes:
            nodes.append({
                "node_id": n.get("node_id", ""),
                "title": n.get("title", ""),
                "page_start": n.get("start_index", 1),
                "page_end": n.get("end_index", 1),
                "summary": n.get("summary", ""),
                "text": n.get("text", ""),
            })
        return nodes

    def get_node_text(self, node: dict, sections: list[dict] = None) -> str:
        """
        Get the full text for a node.
        PageIndex populates 'text' directly on nodes when if_add_node_text='yes'.
        Falls back to summary if text is unavailable.

        The `sections` parameter is kept for backward compatibility but
        is not used — PageIndex provides text directly.
        """
        # If the node already has text from PageIndex, use it
        if node.get("text"):
            return node["text"]

        # Fallback: try to find the node in the tree by node_id
        if self.last_tree:
            all_nodes = structure_to_list(self.last_tree)
            for n in all_nodes:
                if n.get("node_id") == node.get("node_id") and n.get("text"):
                    return n["text"]

        return node.get("summary", "")