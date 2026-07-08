# pipeline/coverage.py



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
                # Only treat uncovered nodes with meaningful content as gaps.
                # Prefer the node's full text, fall back to summary, then title.
                content = node.get("text") or node.get("summary") or node.get("title") or ""
                if len(content) >= min_text_length:
                    gaps.append(node)
        return gaps

    def to_dict(self) -> dict:
        """
        Serialize coverage state for the resume checkpoint (C1). Only the mutable
        per-node state (``task_ids`` + ``covered``) is stored — the ``node`` dict
        is reconstructed from the current node list on restore, so the payload
        stays small and never duplicates the document tree.
        """
        return {
            node_id: {
                "task_ids": list(entry["task_ids"]),
                "covered": bool(entry["covered"]),
            }
            for node_id, entry in self._coverage.items()
        }

    def restore_from(self, data: dict) -> None:
        """
        Restore coverage state produced by :meth:`to_dict`. Only node_ids present
        in the current tracker are restored (a node-set mismatch is handled by the
        caller before this point); unknown node_ids in ``data`` are ignored.
        """
        for node_id, entry in (data or {}).items():
            if node_id in self._coverage:
                self._coverage[node_id]["task_ids"] = list(entry.get("task_ids", []))
                self._coverage[node_id]["covered"] = bool(entry.get("covered", False))

    def coverage_report(self) -> dict:
        total = len(self._coverage)
        covered = sum(1 for e in self._coverage.values() if e["covered"])
        return {
            "total_nodes": total,
            "covered_nodes": covered,
            "gap_nodes": total - covered,
            "coverage_pct": round((covered / total * 100) if total > 0 else 0, 1),
        }