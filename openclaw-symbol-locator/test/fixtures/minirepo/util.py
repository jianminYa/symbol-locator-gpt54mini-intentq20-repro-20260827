"""Utility helpers."""


def save(payload: dict, path: str) -> None:
    """Module-level save — writes payload to path as JSON."""
    import json
    with open(path, "w") as fh:
        json.dump(payload, fh)
