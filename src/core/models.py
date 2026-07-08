# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

class PipelineContext:
    """Shared telemetry, execution context, and metrics carried across the pipeline."""
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.run_id: str = str(uuid.uuid4())
        self.config: Dict[str, Any] = config or {}
        self.state: Dict[str, Any] = {}
        self.errors: List[Dict[str, Any]] = []
        self.metrics: Dict[str, int] = {
            "fetched": 0,
            "transformed": 0,
            "failed": 0,
            "uploaded": 0
        }

    def increment_metric(self, name: str, amount: int = 1):
        if name in self.metrics:
            self.metrics[name] += amount
        else:
            self.metrics[name] = amount

    def record_error(self, component_name: str, item_id: str, error: Exception):
        self.errors.append({
            "component": component_name,
            "item_id": item_id,
            "error_type": type(error).__name__,
            "message": str(error)
        })
        self.increment_metric("failed")

@dataclass
class RawPayload:
    """A boundary container wrapper for native, schema-agnostic source data."""
    data: Any


@dataclass
class SyncResult:
    """Aggregated execution telemetry results returned at the end of a run."""
    run_id: str
    success: bool
    metrics: Dict[str, int]
    errors: List[Dict[str, Any]]
    state: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "success": self.success,
            "metrics": self.metrics,
            "errors": self.errors,
            "error_count": len(self.errors),
            "state": self.state
        }
