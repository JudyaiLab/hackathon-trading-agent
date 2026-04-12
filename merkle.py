"""
Merkle Tree for Validation Artifact Integrity

Generates a Merkle root hash from all validation artifacts (trade intents,
risk checks, strategy checkpoints) so judges can verify no records have
been tampered with post-facto.

Each leaf is SHA-256(canonical JSON of one record). The tree is built
bottom-up with sorted pair hashing (for deterministic ordering).
"""
import hashlib
import json
from pathlib import Path

VALIDATION_DIR = Path(__file__).parent / "validation"

ARTIFACT_FILES = [
    "trade_intents.json",
    "risk_checks.json",
    "strategy_checkpoints.json",
]


def _hash_leaf(record: dict) -> str:
    """Hash a single record as a Merkle leaf."""
    canonical = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _hash_pair(left: str, right: str) -> str:
    """Hash two nodes together (sorted for determinism)."""
    combined = "".join(sorted([left, right]))
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def build_merkle_tree(leaves: list[str]) -> dict:
    """Build a Merkle tree from leaf hashes.

    Returns:
        dict with root, leaf_count, and tree depth
    """
    if not leaves:
        return {"root": hashlib.sha256(b"empty").hexdigest(), "leaf_count": 0, "depth": 0}

    current_level = list(leaves)
    depth = 0

    while len(current_level) > 1:
        next_level = []
        for i in range(0, len(current_level), 2):
            if i + 1 < len(current_level):
                next_level.append(_hash_pair(current_level[i], current_level[i + 1]))
            else:
                next_level.append(current_level[i])  # odd node promoted
        current_level = next_level
        depth += 1

    return {
        "root": current_level[0],
        "leaf_count": len(leaves),
        "depth": depth,
    }


def compute_artifact_merkle() -> dict:
    """Compute Merkle root across all validation artifacts.

    Returns:
        dict with merkle_root, total_records, per_file stats, and algorithm info
    """
    all_leaves: list[str] = []
    file_stats: dict[str, dict] = {}

    for fname in ARTIFACT_FILES:
        fpath = VALIDATION_DIR / fname
        if not fpath.exists():
            file_stats[fname] = {"records": 0, "status": "missing"}
            continue

        try:
            data = json.loads(fpath.read_text())
            records = data.get("records", [])
            leaves = [_hash_leaf(r) for r in records]
            all_leaves.extend(leaves)

            # Per-file Merkle root for granular verification
            file_tree = build_merkle_tree(leaves)
            file_stats[fname] = {
                "records": len(records),
                "merkle_root": file_tree["root"],
            }
        except (json.JSONDecodeError, TypeError) as e:
            file_stats[fname] = {"records": 0, "status": f"error: {e}"}

    tree = build_merkle_tree(all_leaves)

    return {
        "merkle_root": tree["root"],
        "algorithm": "SHA-256 sorted-pair Merkle tree",
        "total_records": tree["leaf_count"],
        "tree_depth": tree["depth"],
        "files": file_stats,
    }


def verify_record(record: dict, expected_leaf_hash: str) -> bool:
    """Verify a single record matches its expected leaf hash."""
    return _hash_leaf(record) == expected_leaf_hash


if __name__ == "__main__":
    result = compute_artifact_merkle()
    print(json.dumps(result, indent=2))
