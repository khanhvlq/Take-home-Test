from __future__ import annotations

import gzip
import os
import shutil
from typing import Any


def apply_transformations(local_filepath: str, transformations: list[str] | None) -> str:
    if not transformations:
        return local_filepath

    output_path = local_filepath
    for transformation in transformations:
        action = (transformation or "").strip().lower()
        if action in {"", "noop"}:
            continue
        if action == "gzip":
            gz_path = f"{output_path}.gz"
            with open(output_path, "rb") as src, gzip.open(gz_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.remove(output_path)
            output_path = gz_path
            continue
        raise ValueError(f"Unsupported transformation: {transformation}")

    return output_path


def normalize_transformations(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value]
    if isinstance(raw_value, str):
        return [raw_value]
    return [str(raw_value)]
