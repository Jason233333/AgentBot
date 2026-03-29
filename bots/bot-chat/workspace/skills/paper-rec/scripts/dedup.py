"""Deduplication module for paper recommender.

Handles tracking of published paper IDs and deduplicating candidate papers
against both historical records and within the current batch.
"""

import json
import os
from typing import List, Set


def load_published_ids(filepath: str) -> Set[str]:
    """Load published paper IDs from a text file (one ID per line).

    Creates the file if it does not exist.
    """
    if not os.path.exists(filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            pass  # create empty file
        return set()

    with open(filepath, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_published_id(filepath: str, paper_id: str) -> None:
    """Append a single paper ID to the published IDs file.

    Creates the file if it does not exist.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(paper_id.strip() + "\n")


def load_papers_db(filepath: str) -> List[dict]:
    """Load the historical papers database from a JSON file.

    Creates the file with an empty list if it does not exist.
    """
    if not os.path.exists(filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_papers_db(filepath: str, papers: List[dict]) -> None:
    """Save the papers list to a JSON file.

    Creates parent directories if they do not exist.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)


def dedup(candidates: List[dict], published_ids: Set[str]) -> List[dict]:
    """Remove duplicates from candidate papers.

    Two-stage deduplication:
    1. Filter out papers whose ID is already in published_ids.
    2. Deduplicate within the current batch by ID (first occurrence wins).

    Each candidate dict must have an 'id' key.
    """
    seen: Set[str] = set()
    result: List[dict] = []

    for paper in candidates:
        pid = paper.get("id", "").strip()
        if not pid:
            continue
        if pid in published_ids:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        result.append(paper)

    return result
