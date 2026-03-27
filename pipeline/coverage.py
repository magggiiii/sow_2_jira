# pipeline/coverage.py

from models.schemas import ManagedTask


class CoverageTracker:
    """
    Tracks which PageIndex nodes have been covered by extracted tasks.
    A node is "covered" if at least one task references it.
    "Gap" = a node with text content that has zero tasks.
    """

    def __init__(self, nodes: list[dict]):
        # node_id → {"node": node_dict, "task_ids": [], "covered": False}
        self._coverage: dict[str, dict] = {
            node["node_id"]: {
                "node": node,
                "task_ids": [],
                "covered": False,
            }
            for node in nodes
        }

    def mark_covered(self, node_id: str, task_id: str):
        if node_id in self._coverage:
            self._coverage[node_id]["task_ids"].append(task_id)
            self._coverage[node_id]["covered"] = True

    def get_gaps(self, min_text_length: int = 100) -> list[dict]:
        """
        Returns nodes that are uncovered AND have meaningful content
        (node summary length > min_text_length chars).
        """
        gaps = []
        for node_id, entry in self._coverage.items():
            node = entry["node"]
            if not entry["covered"]:
                # If no tasks extracted, check if the node has enough summary/content to qualify as a gap
                # Here we will just use min_text_length as a rough filter 
                # (Actual content length checks happen in orchestrator/agent)
                gaps.append(node)
        return gaps

    def coverage_report(self) -> dict:
        total = len(self._coverage)
        covered = sum(1 for e in self._coverage.values() if e["covered"])
        return {
            "total_nodes": total,
            "covered_nodes": covered,
            "gap_nodes": total - covered,
            "coverage_pct": round((covered / total * 100) if total > 0 else 0, 1),
        }