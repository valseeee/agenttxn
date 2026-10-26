from pathlib import Path
from typing import Any
import hashlib, json, os, tempfile
from ..errors import ActionExecutionError, OutcomeUnknownError, PreflightError
from ..models import ActionSpec, ReconcileOutcome
from .base import AdapterContext
from .capabilities import ProviderCapabilities

class DemoAdapter:
    """Deterministic fake provider; optionally persists its simulated world to JSON."""
    kinds = frozenset({"demo.create", "demo.fail", "demo.unknown"})

    def __init__(self, state_path: str | Path | None = None):
        self.state_path = str(state_path) if state_path is not None else None
        self.effects: dict[str, dict[str, Any]] = {}
        self._reload()

    def _reload(self):
        if self.state_path and Path(self.state_path).exists():
            data=json.loads(Path(self.state_path).read_text(encoding="utf-8"))
            if not isinstance(data,dict) or not all(isinstance(value,dict) for value in data.values()):
                raise ValueError("demo provider state must be an object of resource objects")
            self.effects=data

    def _save(self):
        if not self.state_path: return
        path = Path(self.state_path); path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.effects, f, ensure_ascii=False, sort_keys=True, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def validate(self, action: ActionSpec):
        if action.kind not in self.kinds: raise PreflightError(f"unsupported demo action: {action.kind}")
        if action.kind in {"demo.create", "demo.unknown"} and action.effect.value == "read_only":
            raise PreflightError(f"{action.kind} changes simulated state and cannot be marked read_only")
        if action.kind in {"demo.create", "demo.unknown"} and not action.payload.get("name"):
            raise PreflightError(f"{action.kind} requires payload.name")

    def preflight(self, context: AdapterContext, action: ActionSpec):
        self._reload()
        self.validate(action)
        return {"exists": action.idempotency_key in self.effects}

    def execute(self, context: AdapterContext, action: ActionSpec, preflight: dict[str, Any]):
        self._reload()
        if action.kind == "demo.fail": raise ActionExecutionError("simulated deterministic failure")
        key = action.idempotency_key
        if key in self.effects: return dict(self.effects[key])
        resource_id = "demo-" + hashlib.sha256(key.encode()).hexdigest()[:12]
        result = {"resource_id": resource_id, "name": action.payload["name"], "idempotency_key": key}
        self.effects[key] = result; self._save()
        if action.kind == "demo.unknown":
            raise OutcomeUnknownError("simulated lost response after the effect was created")
        return result

    def compensate(self, context, action, result, preflight):
        self._reload()
        self.effects.pop(action.idempotency_key, None); self._save()
        return {"removed": True, "resource_id": result.get("resource_id")}

    def reconcile(self, context, action, phase, result, preflight):
        self._reload()
        exists = action.idempotency_key in self.effects
        if phase == "forward":
            if exists: return ReconcileOutcome.SUCCEEDED, dict(self.effects[action.idempotency_key])
            return ReconcileOutcome.NOT_EXECUTED, None
        if not exists: return ReconcileOutcome.SUCCEEDED, {"removed": True}
        return ReconcileOutcome.NOT_EXECUTED, None

    def can_compensate(self, action):
        return action.effect.value == "reversible" and action.kind in {"demo.create", "demo.unknown"}

    def capabilities(self, action):
        # Guarantees concern this simulated world only, not concurrent JSON writers.
        return ProviderCapabilities(native_idempotency=True,stable_lookup=True)
