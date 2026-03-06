from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class CatalogAssetMetadata:
    role: str
    conn_type: str
    conn_id: str
    base_path: str
    uri: str

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "conn_type": self.conn_type,
            "conn_id": self.conn_id,
            "base_path": self.base_path,
            "uri": self.uri,
        }


@dataclass(slots=True)
class CatalogPipelineMetadata:
    pipeline_id: str
    generated_at: str
    assets: list[CatalogAssetMetadata] = field(default_factory=list)
    processing_components: list[dict[str, Any]] = field(default_factory=list)
    orchestration: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "generated_at": self.generated_at,
            "assets": [asset.to_dict() for asset in self.assets],
            "processing_components": self.processing_components,
            "orchestration": self.orchestration,
        }


class PipelineMetadataBuilder:
    def __init__(self, pipeline_id: str) -> None:
        self._pipeline_id = pipeline_id
        self._assets: list[CatalogAssetMetadata] = []
        self._processing_components: list[dict[str, Any]] = []
        self._orchestration: dict[str, Any] = {}

    @staticmethod
    def _build_uri(conn_type: str, conn_id: str, base_path: str) -> str:
        normalized = base_path if base_path.startswith("/") else f"/{base_path}"
        return f"{conn_type}://{conn_id}{normalized}"

    def set_source(self, conn_type: str, conn_id: str, base_path: str) -> "PipelineMetadataBuilder":
        self._assets.append(
            CatalogAssetMetadata(
                role="source",
                conn_type=conn_type,
                conn_id=conn_id,
                base_path=base_path,
                uri=self._build_uri(conn_type=conn_type, conn_id=conn_id, base_path=base_path),
            )
        )
        return self

    def set_target(self, conn_type: str, conn_id: str, base_path: str) -> "PipelineMetadataBuilder":
        self._assets.append(
            CatalogAssetMetadata(
                role="target",
                conn_type=conn_type,
                conn_id=conn_id,
                base_path=base_path,
                uri=self._build_uri(conn_type=conn_type, conn_id=conn_id, base_path=base_path),
            )
        )
        return self

    def add_processing_component(
        self,
        name: str,
        component_type: str,
        config: dict[str, Any] | None = None,
    ) -> "PipelineMetadataBuilder":
        self._processing_components.append(
            {
                "name": name,
                "component_type": component_type,
                "config": config or {},
            }
        )
        return self

    def set_orchestration(self, orchestration: dict[str, Any]) -> "PipelineMetadataBuilder":
        self._orchestration = dict(orchestration)
        return self

    def build(self) -> CatalogPipelineMetadata:
        return CatalogPipelineMetadata(
            pipeline_id=self._pipeline_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            assets=self._assets,
            processing_components=self._processing_components,
            orchestration=self._orchestration,
        )
