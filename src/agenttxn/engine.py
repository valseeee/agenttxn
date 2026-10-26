from __future__ import annotations
from pathlib import Path
from typing import Iterable
import hashlib, uuid, threading
from functools import wraps
from .adapters.base import AdapterContext, ActionAdapter
from .adapters.capabilities import normalize_evidence, provider_capabilities
from .errors import (ActionExecutionError, AdapterNotFoundError, ApprovalRequiredError,
    ApprovalStaleError, InvalidTransitionError, OutcomeUnknownError, PlanFrozenError,
    PolicyVersionMismatchError, ConcurrentModificationError, PreflightError)
from .models import (ActionSpec, CommitReport, EffectClass, PreparationReport, ReconcileOutcome,
    StepStatus, TransactionStatus, VerificationReport, canonical_json)
from .policy import Policy
from .store import SQLiteStore

class Engine:
    def __init__(self, store: SQLiteStore, *, policy: Policy | None = None, adapters: Iterable[ActionAdapter] = ()):
        self.store=store; self.policy=policy or Policy.default_safe(); self._adapters={}; self._thread_owner=threading.local()
        for adapter in adapters: self.register(adapter)

    @classmethod
    def open(cls, path: str | Path, *, policy=None, adapters=()):
        return cls(SQLiteStore(path), policy=policy, adapters=adapters)

    def register(self, adapter):
        for kind in adapter.kinds:
            if kind in self._adapters: raise ValueError(f"adapter already registered: {kind}")
            self._adapters[kind]=adapter

    def adapter_for(self, action):
        try: return self._adapters[action.kind]
        except KeyError as e: raise AdapterNotFoundError(f"no adapter registered for {action.kind}") from e

    def capabilities_for(self, action):
        return provider_capabilities(self.adapter_for(action),action)

    def create(self, *, intent, idempotency_key, metadata=None):
        if not intent.strip() or not idempotency_key.strip(): raise ValueError("intent and idempotency_key are required")
        tx=self.store.create_transaction(str(uuid.uuid4()),intent,idempotency_key,self.policy.identity,metadata or {})
        return TransactionHandle(self,tx.id)

    def load(self, transaction_id):
        self.store.get_transaction(transaction_id)
        return TransactionHandle(self,transaction_id)

    def list(self, limit=50): return self.store.list_transactions(limit)

    def _transition(self, txid, target, event_payload=None):
        return self.store.transition_transaction(txid,target,event_payload)

    def _assert_policy_current(self, tx):
        if tx.policy_version not in {self.policy.identity,self.policy.legacy_identity}:
            raise PolicyVersionMismatchError(
                "transaction was created under a different policy configuration; "
                "reopen it with the original policy or create a new transaction"
            )

    @staticmethod
    def _error(exc):
        # Adapter exceptions can contain access tokens, request bodies, or PII.
        # Do not persist exception text into the audit log or database.
        return {"type":type(exc).__name__,"message":"provider operation failed; inspect private provider logs"}

    def _plan(self, tx, steps, *, legacy=False):
        return {"intent":tx.intent,"policy_version":tx.policy_version,
                "steps":[{"sequence":s.sequence,"action":s.action.to_dict(),
                          **({} if legacy else {"preflight":s.preflight})} for s in steps]}

    def _plan_hash(self, tx, steps, *, legacy=False):
        return hashlib.sha256(canonical_json(self._plan(tx,steps,legacy=legacy)).encode()).hexdigest()

    def _plan_valid(self, tx, steps):
        return tx.plan_hash is not None and tx.plan_hash in {
            self._plan_hash(tx,steps),self._plan_hash(tx,steps,legacy=True)}

def _worker_guard(fn):
    @wraps(fn)
    def guarded(self, *args, **kwargs):
        owners=getattr(self.engine._thread_owner,"owners",None)
        if owners is None:
            owners={}; self.engine._thread_owner.owners=owners
        if self.id in owners:
            return fn(self,*args,**kwargs)
        token=uuid.uuid4().hex
        takeover=fn.__name__ in {"reconcile","resume"} and kwargs.get("confirm_worker_stopped",False)
        if takeover and self.record.status in {
            TransactionStatus.COMMITTED,TransactionStatus.COMPENSATED,TransactionStatus.FAILED,
            TransactionStatus.PARTIAL_FAILURE,TransactionStatus.ABORTED,
            TransactionStatus.PREPARATION_FAILED}:
            raise InvalidTransitionError("takeover is not valid for a finalized transaction")
        self.engine.store.claim_worker(self.id,token,force=takeover)
        owners[self.id]=token
        try:
            return fn(self,*args,**kwargs)
        finally:
            owners.pop(self.id,None)
            self.engine.store.release_worker(self.id,token)
    return guarded

class TransactionHandle:
    def __init__(self, engine, transaction_id): self.engine=engine; self.id=transaction_id
    @property
    def record(self): return self.engine.store.get_transaction(self.id)
    @property
    def steps(self): return self.engine.store.get_steps(self.id)

    def add(self, action: ActionSpec):
        self.engine._assert_policy_current(self.record)
        if self.record.status != TransactionStatus.CREATED:
            raise PlanFrozenError("actions can only be added while CREATED")
        canonical_json(action.to_dict())
        return self.engine.store.add_step(self.id,action)

    @_worker_guard
    def prepare(self):
        self.engine._assert_policy_current(self.record)
        if self.record.status != TransactionStatus.CREATED:
            raise InvalidTransitionError("prepare requires CREATED")
        self.engine._transition(self.id,TransactionStatus.PREPARING)
        return self._finish_prepare()

    def _check_preconditions(self, action, preflight):
        for key, expected in action.preconditions.items():
            if key not in preflight or preflight[key] != expected:
                raise PreflightError(f"precondition failed: {key}")

    def _finish_prepare(self):
        try:
            steps=self.steps; actions=[s.action for s in steps]
            self.engine.policy.validate(actions)
            for s in steps:
                adapter=self.engine.adapter_for(s.action); adapter.validate(s.action)
                preflight=adapter.preflight(AdapterContext(self.id,s.id,"preflight"),s.action)
                if not isinstance(preflight,dict): raise PreflightError("preflight must return a JSON object")
                self._check_preconditions(s.action,preflight)
                self.engine.store.update_step(s.id,status=StepStatus.PREFLIGHTED,preflight=preflight,event_type="STEP_PREFLIGHTED")
            tx=self.record; plan_hash=self.engine._plan_hash(tx,self.steps)
            self.engine.store.update_transaction(self.id,plan_hash=plan_hash,event_type="PLAN_FROZEN",event_payload={"plan_hash":plan_hash})
            needs=self.engine.policy.approval_required(actions)
            target=TransactionStatus.AWAITING_APPROVAL if needs else TransactionStatus.PREPARED
            self.engine._transition(self.id,target,{"approval_required":needs})
            return PreparationReport(self.id,target,plan_hash,needs)
        except Exception as exc:
            if self.record.status == TransactionStatus.PREPARING:
                self.engine.store.update_transaction(self.id,last_error=self.engine._error(exc),event_type="PREPARATION_ERROR")
                self.engine._transition(self.id,TransactionStatus.PREPARATION_FAILED)
            raise

    def approve(self, *, approved_by, reason=None):
        tx=self.record
        self.engine._assert_policy_current(tx)
        if tx.status != TransactionStatus.AWAITING_APPROVAL:
            raise InvalidTransitionError(f"approve requires AWAITING_APPROVAL, got {tx.status.value}")
        if not approved_by.strip(): raise ValueError("approved_by is required")
        if not self.engine._plan_valid(tx,self.steps): raise ApprovalStaleError("plan hash changed")
        return self.engine.store.add_approval(self.id,tx.plan_hash,approved_by,reason)

    def abort(self, reason="aborted by user"):
        if self.record.status not in {TransactionStatus.CREATED,TransactionStatus.PREPARED,TransactionStatus.AWAITING_APPROVAL,TransactionStatus.APPROVED}:
            raise InvalidTransitionError(f"cannot abort from {self.record.status.value}")
        self.engine.store.update_transaction(self.id,last_error={"message":reason},event_type="TRANSACTION_ABORT_REQUESTED")
        return self.engine._transition(self.id,TransactionStatus.ABORTED)

    def _approval_valid(self, tx):
        a=self.engine.store.get_active_approval(self.id)
        return bool(a and a.plan_hash == tx.plan_hash)

    @_worker_guard
    def commit(self):
        tx=self.record
        self.engine._assert_policy_current(tx)
        if tx.plan_hash and not self.engine._plan_valid(tx,self.steps):
            raise ApprovalStaleError("frozen plan or preflight data changed")
        if tx.status == TransactionStatus.AWAITING_APPROVAL:
            raise ApprovalRequiredError("human approval required")
        if tx.status == TransactionStatus.APPROVED and not self._approval_valid(tx):
            raise ApprovalStaleError("approval missing or plan hash mismatch")
        if tx.status not in {TransactionStatus.PREPARED,TransactionStatus.APPROVED,TransactionStatus.COMMITTING}:
            raise InvalidTransitionError(f"cannot commit from {tx.status.value}")

        current_steps=self.steps
        unknown=[s for s in current_steps if s.status in {StepStatus.UNKNOWN,StepStatus.COMPENSATION_UNKNOWN}]
        if unknown:
            if tx.status == TransactionStatus.COMMITTING:
                self.engine._transition(self.id,TransactionStatus.NEEDS_RECONCILIATION,{"reason":"unknown attempt outcome"})
            return self._report(TransactionStatus.NEEDS_RECONCILIATION,"unknown outcome; reconcile before retry",unknown[0].id)
        inflight=[s for s in current_steps if s.status in {StepStatus.EXECUTING,StepStatus.COMPENSATING}]
        if inflight:
            # A second caller must not mistake another worker's live claim for a crash.
            return self._report(tx.status,"a worker already owns an in-flight step",inflight[0].id)

        persisted_failure=next((s for s in current_steps if s.status == StepStatus.FAILED),None)
        if persisted_failure and tx.status == TransactionStatus.COMMITTING:
            # Recover a crash that occurred after the failed step was persisted but before
            # failure handling/compensation updated the transaction state.
            return self._handle_failure(persisted_failure,ActionExecutionError("recovering a persisted failed step"))

        if tx.status in {TransactionStatus.PREPARED,TransactionStatus.APPROVED}:
            self.engine._transition(self.id,TransactionStatus.COMMITTING)

        failure=None
        failed=None
        for s in self.steps:
            if s.status == StepStatus.SUCCEEDED:
                continue
            if s.status == StepStatus.FAILED:
                failed=s; failure=ActionExecutionError("recovering a persisted failed step"); break
            if s.status not in {StepStatus.PENDING,StepStatus.PREFLIGHTED}:
                continue
            adapter=self.engine.adapter_for(s.action)
            no=self.engine.store.next_attempt_no(s.id,"forward")
            fp=hashlib.sha256(canonical_json(s.action.to_dict()).encode()).hexdigest()
            aid=self.engine.store.claim_step(
                self.id,s.id,attempt_no=no,phase="forward",fingerprint=fp,
                allowed_statuses={StepStatus.PENDING,StepStatus.PREFLIGHTED},target_status=StepStatus.EXECUTING
            )
            if aid is None:
                continue
            ctx=AdapterContext(self.id,s.id,aid,"forward")
            claimed=self.engine.store.get_step(s.id)
            try:
                if s.action.preconditions:
                    current_preflight=adapter.preflight(ctx,s.action)
                    try: self._check_preconditions(s.action,current_preflight)
                    except PreflightError as exc: raise ActionExecutionError("runtime precondition mismatch") from exc
                result=adapter.execute(ctx,s.action,claimed.preflight or {})
                if not isinstance(result,dict): raise ValueError("execute must return a JSON object")
                canonical_json(result)  # validate before recording a successful external write
            except OutcomeUnknownError as exc:
                err=self.engine._error(exc)
                self.engine.store.finish_attempt(aid,"UNKNOWN",error=err)
                self.engine.store.update_step(s.id,status=StepStatus.UNKNOWN,last_error=err,event_type="STEP_OUTCOME_UNKNOWN")
                self.engine.store.update_transaction(self.id,last_error=err,event_type="RECONCILIATION_REQUIRED",event_payload={"step_id":s.id})
                self.engine._transition(self.id,TransactionStatus.NEEDS_RECONCILIATION,{"step_id":s.id})
                return self._report(TransactionStatus.NEEDS_RECONCILIATION,"write outcome unknown; blind retry prohibited",s.id)
            except ActionExecutionError as exc:
                err=self.engine._error(exc)
                self.engine.store.finish_attempt(aid,"FAILED",error=err)
                self.engine.store.update_step(s.id,status=StepStatus.FAILED,last_error=err,event_type="STEP_EXECUTION_FAILED")
                failed=self.engine.store.get_step(s.id); failure=exc; break
            except Exception as exc:
                err=self.engine._error(exc)
                self.engine.store.finish_attempt(aid,"UNKNOWN",error=err)
                self.engine.store.update_step(s.id,status=StepStatus.UNKNOWN,last_error=err,event_type="STEP_OUTCOME_UNKNOWN")
                self.engine._transition(self.id,TransactionStatus.NEEDS_RECONCILIATION,{"step_id":s.id})
                return self._report(TransactionStatus.NEEDS_RECONCILIATION,"unexpected provider error; reconcile before retry",s.id)
            else:
                self.engine.store.finish_attempt(aid,"SUCCEEDED",response=result)
                self.engine.store.update_step(s.id,status=StepStatus.SUCCEEDED,result=result,event_type="STEP_EXECUTION_SUCCEEDED")

        latest=self.steps
        unknown=[s for s in latest if s.status in {StepStatus.UNKNOWN,StepStatus.COMPENSATION_UNKNOWN}]
        if unknown:
            if self.record.status == TransactionStatus.COMMITTING:
                self.engine._transition(self.id,TransactionStatus.NEEDS_RECONCILIATION,{"reason":"unknown attempt outcome"})
            return self._report(TransactionStatus.NEEDS_RECONCILIATION,"unknown outcome; reconcile before retry",unknown[0].id)
        inflight=[s for s in latest if s.status in {StepStatus.EXECUTING,StepStatus.COMPENSATING}]
        if inflight:
            return self._report(self.record.status,"a worker still owns an in-flight step",inflight[0].id)
        if failed is None:
            failed=next((s for s in latest if s.status == StepStatus.FAILED),None)
        if failed:
            return self._handle_failure(failed,failure or ActionExecutionError("recovering a persisted failed step"))
        if any(s.status in {StepStatus.PENDING,StepStatus.PREFLIGHTED} for s in latest):
            return self._report(TransactionStatus.COMMITTING,"some steps remain unclaimed; call resume to continue")
        # Do not overwrite a concurrent transition into compensation/reconciliation.
        current_tx=self.record
        if current_tx.status != TransactionStatus.COMMITTING:
            return self._report(current_tx.status,"transaction state changed concurrently")
        try:
            self.engine._transition(self.id,TransactionStatus.COMMITTED)
        except InvalidTransitionError:
            current=self.record.status
            if current != TransactionStatus.COMMITTING:
                return self._report(current,"transaction state changed concurrently")
            raise
        return self._report(TransactionStatus.COMMITTED,"all steps committed")

    def _handle_failure(self, failed, exc):
        current_status=self.record.status
        if current_status in {TransactionStatus.COMMITTED,TransactionStatus.COMPENSATED,TransactionStatus.FAILED,
                              TransactionStatus.PARTIAL_FAILURE,TransactionStatus.ABORTED,TransactionStatus.PREPARATION_FAILED}:
            return self._report(current_status,"transaction state already finalized",failed.id)
        if current_status == TransactionStatus.NEEDS_RECONCILIATION:
            return self._report(current_status,"reconciliation must finish before failure handling continues",failed.id)
        successful=[s for s in self.steps if s.status == StepStatus.SUCCEEDED]
        if not successful:
            self.engine.store.update_transaction(self.id,last_error=self.engine._error(exc),event_type="TRANSACTION_FAILED")
            self.engine._transition(self.id,TransactionStatus.FAILED)
            return self._report(TransactionStatus.FAILED,"a step failed with a known outcome",failed.id)
        reversible=[]; unhandled=[]
        for s in reversed(successful):
            if s.action.effect == EffectClass.READ_ONLY: continue
            if self.engine.adapter_for(s.action).can_compensate(s.action): reversible.append(s)
            else: unhandled.append(s)
        if not reversible and not unhandled:
            self.engine.store.update_transaction(self.id,last_error=self.engine._error(exc),event_type="TRANSACTION_FAILED")
            self.engine._transition(self.id,TransactionStatus.FAILED)
            return self._report(TransactionStatus.FAILED,"a step failed with a known outcome",failed.id)
        if not reversible and unhandled:
            self.engine._transition(self.id,TransactionStatus.PARTIAL_FAILURE,{"reason":"effects cannot be compensated"})
            return self._report(TransactionStatus.PARTIAL_FAILURE,"some side effects cannot be compensated",failed.id)
        self.engine._transition(self.id,TransactionStatus.COMPENSATING,{"failed_step_id":failed.id})
        return self._run_compensation(failed,reversible,unhandled,exc)

    def _run_compensation(self, failed, reversible, unhandled, exc=None):
        partial=bool(unhandled) or any(s.status==StepStatus.COMPENSATION_FAILED for s in self.steps)
        for s in reversible:
            current=self.engine.store.get_step(s.id)
            if current.status == StepStatus.COMPENSATED:
                continue
            if current.status == StepStatus.COMPENSATING:
                return self._report(TransactionStatus.COMPENSATING,"a worker already owns compensation",s.id)
            if current.status != StepStatus.SUCCEEDED:
                partial = partial or current.status == StepStatus.COMPENSATION_FAILED
                continue
            adapter=self.engine.adapter_for(s.action)
            no=self.engine.store.next_attempt_no(s.id,"compensation")
            fp=hashlib.sha256(canonical_json({"action":s.action.to_dict(),"phase":"compensation"}).encode()).hexdigest()
            aid=self.engine.store.claim_step(
                self.id,s.id,attempt_no=no,phase="compensation",fingerprint=fp,
                allowed_statuses={StepStatus.SUCCEEDED},target_status=StepStatus.COMPENSATING
            )
            if aid is None:
                current=self.engine.store.get_step(s.id)
                if current.status == StepStatus.COMPENSATING:
                    return self._report(TransactionStatus.COMPENSATING,"a worker already owns compensation",s.id)
                if current.status == StepStatus.COMPENSATED:
                    continue
                partial=True
                continue
            current=self.engine.store.get_step(s.id)
            try:
                result=adapter.compensate(AdapterContext(self.id,s.id,aid,"compensation"),s.action,current.result or {},current.preflight)
                if not isinstance(result,dict): raise ValueError("compensate must return a JSON object")
                canonical_json(result)
            except OutcomeUnknownError as comp_exc:
                err=self.engine._error(comp_exc); self.engine.store.finish_attempt(aid,"UNKNOWN",error=err)
                self.engine.store.update_step(s.id,status=StepStatus.COMPENSATION_UNKNOWN,last_error=err,event_type="COMPENSATION_OUTCOME_UNKNOWN")
                self.engine._transition(self.id,TransactionStatus.NEEDS_RECONCILIATION,{"step_id":s.id,"phase":"compensation"})
                return self._report(TransactionStatus.NEEDS_RECONCILIATION,"compensation outcome unknown",s.id)
            except ActionExecutionError as comp_exc:
                err=self.engine._error(comp_exc); self.engine.store.finish_attempt(aid,"FAILED",error=err)
                self.engine.store.update_step(s.id,status=StepStatus.COMPENSATION_FAILED,last_error=err,event_type="COMPENSATION_FAILED")
                partial=True
            except Exception as comp_exc:
                err=self.engine._error(comp_exc); self.engine.store.finish_attempt(aid,"UNKNOWN",error=err)
                self.engine.store.update_step(s.id,status=StepStatus.COMPENSATION_UNKNOWN,last_error=err,event_type="COMPENSATION_OUTCOME_UNKNOWN")
                self.engine._transition(self.id,TransactionStatus.NEEDS_RECONCILIATION,{"step_id":s.id,"phase":"compensation"})
                return self._report(TransactionStatus.NEEDS_RECONCILIATION,"unexpected compensation error; reconcile before retry",s.id)
            else:
                self.engine.store.finish_attempt(aid,"SUCCEEDED",response=result)
                self.engine.store.update_step(s.id,status=StepStatus.COMPENSATED,compensation=result,event_type="COMPENSATION_SUCCEEDED")
        latest=self.steps
        current_status=self.record.status
        if current_status != TransactionStatus.COMPENSATING:
            return self._report(current_status,"transaction state changed while compensation was running")
        if any(s.status == StepStatus.COMPENSATING for s in latest):
            return self._report(TransactionStatus.COMPENSATING,"compensation is still in progress")
        if partial or any(s.status==StepStatus.COMPENSATION_FAILED for s in latest):
            self.engine._transition(self.id,TransactionStatus.PARTIAL_FAILURE,{"reason":"compensation incomplete"})
            return self._report(TransactionStatus.PARTIAL_FAILURE,"some side effects remain",failed.id if failed else None)
        self.engine._transition(self.id,TransactionStatus.COMPENSATED)
        return self._report(TransactionStatus.COMPENSATED,"effects compensated; this is not universal rollback",failed.id if failed else None)

    @_worker_guard
    def reconcile(self, *, confirm_worker_stopped=False):
        self.engine._assert_policy_current(self.record)
        current=self.record.status
        if current in {TransactionStatus.COMMITTING,TransactionStatus.COMPENSATING} and not confirm_worker_stopped:
            raise ConcurrentModificationError("explicit reconciliation of an in-flight transaction requires confirm_worker_stopped=True")
        if current not in {TransactionStatus.NEEDS_RECONCILIATION,TransactionStatus.COMMITTING,TransactionStatus.COMPENSATING}:
            raise InvalidTransitionError(
                f"reconcile requires NEEDS_RECONCILIATION, COMMITTING, or COMPENSATING; got {current.value}"
            )
        if current in {TransactionStatus.COMMITTING,TransactionStatus.COMPENSATING}:
            # This method is the explicit crash-recovery boundary. Call it only after
            # confirming that the worker which owned the in-flight attempt has stopped.
            self.engine._transition(self.id,TransactionStatus.NEEDS_RECONCILIATION,
                                    {"reason":"explicit reconciliation requested"})
        unresolved=[s for s in self.steps if s.status in {StepStatus.EXECUTING,StepStatus.UNKNOWN,StepStatus.COMPENSATING,StepStatus.COMPENSATION_UNKNOWN}]
        unknown=[]
        for s in unresolved:
            phase="compensation" if s.status in {StepStatus.COMPENSATING,StepStatus.COMPENSATION_UNKNOWN} else "forward"
            adapter=self.engine.adapter_for(s.action)
            try:
                evidence=adapter.reconcile(AdapterContext(self.id,s.id,"reconcile",phase),s.action,phase,s.result,s.preflight)
                outcome,result,level=normalize_evidence(evidence)
            except Exception as exc:
                self.engine.store.update_step(s.id,last_error=self.engine._error(exc),event_type="RECONCILIATION_QUERY_FAILED")
                unknown.append(s); continue
            self.engine.store.record_event(self.id,"RECONCILIATION_EVIDENCE",
                {"phase":phase,"outcome":outcome.value,"evidence_level":level.value},s.id)
            if outcome == ReconcileOutcome.UNKNOWN:
                unknown.append(s); continue
            attempt_status = "RECONCILED_SUCCEEDED" if outcome == ReconcileOutcome.SUCCEEDED else "RECONCILED_NOT_EXECUTED"
            self.engine.store.resolve_open_attempt(s.id,phase,attempt_status,response=result)
            if phase == "forward":
                if outcome == ReconcileOutcome.SUCCEEDED:
                    self.engine.store.update_step(s.id,status=StepStatus.SUCCEEDED,result=result or {},event_type="RECONCILIATION_CONFIRMED_SUCCESS")
                else:
                    self.engine.store.update_step(s.id,status=StepStatus.PENDING,event_type="RECONCILIATION_CONFIRMED_NOT_EXECUTED")
            else:
                if outcome == ReconcileOutcome.SUCCEEDED:
                    self.engine.store.update_step(s.id,status=StepStatus.COMPENSATED,compensation=result or {"reconciled":True},event_type="COMPENSATION_RECONCILED_SUCCESS")
                else:
                    self.engine.store.update_step(s.id,status=StepStatus.SUCCEEDED,event_type="COMPENSATION_RECONCILED_NOT_EXECUTED")
        if unknown:
            return self._report(TransactionStatus.NEEDS_RECONCILIATION,f"{len(unknown)} outcome(s) still unknown; no blind retry",unknown[0].id)
        steps=self.steps
        events=self.engine.store.get_events(self.id)
        compensation_started=any(e.event_type=="COMPENSATION_STARTED" for e in events)
        failed=next((s for s in steps if s.status==StepStatus.FAILED),None)
        if compensation_started and failed:
            remaining=[]; unhandled=[]
            for step in reversed(self.steps):
                if step.status != StepStatus.SUCCEEDED or step.action.effect == EffectClass.READ_ONLY:
                    continue
                adapter=self.engine.adapter_for(step.action)
                if adapter.can_compensate(step.action): remaining.append(step)
                else: unhandled.append(step)
            self.engine._transition(self.id,TransactionStatus.COMPENSATING,{"reason":"resume compensation after reconciliation"})
            if remaining:
                return self._run_compensation(failed,remaining,unhandled,ActionExecutionError("resuming compensation"))
            if unhandled or any(s.status==StepStatus.COMPENSATION_FAILED for s in self.steps):
                self.engine._transition(self.id,TransactionStatus.PARTIAL_FAILURE,{"reason":"compensation incomplete"})
                return self._report(TransactionStatus.PARTIAL_FAILURE,"compensation failed or some effects remain")
            self.engine._transition(self.id,TransactionStatus.COMPENSATED)
            return self._report(TransactionStatus.COMPENSATED,"compensation reconciled")
        self.engine._transition(self.id,TransactionStatus.COMMITTING,{"reason":"forward outcomes reconciled"})
        return self.commit()

    @_worker_guard
    def resume(self, *, confirm_worker_stopped=False):
        tx=self.record
        self.engine._assert_policy_current(tx)
        if tx.status == TransactionStatus.CREATED:
            return self.prepare()
        if tx.status == TransactionStatus.PREPARING:
            return self._finish_prepare()
        if tx.status == TransactionStatus.NEEDS_RECONCILIATION:
            return self.reconcile()
        if tx.status == TransactionStatus.COMMITTING:
            steps=self.steps
            unresolved=[s for s in steps if s.status in {StepStatus.EXECUTING,StepStatus.UNKNOWN,StepStatus.COMPENSATING,StepStatus.COMPENSATION_UNKNOWN}]
            if unresolved:
                return self._report(TransactionStatus.COMMITTING,
                    "an in-flight attempt exists; after confirming the old worker has stopped, call reconcile()",
                    unresolved[0].id)
            failed=next((s for s in steps if s.status==StepStatus.FAILED),None)
            if failed:
                return self._handle_failure(failed,ActionExecutionError("recovering a persisted failed step"))
            return self.commit()
        if tx.status == TransactionStatus.COMPENSATING:
            steps=self.steps
            unresolved=[s for s in steps if s.status in {StepStatus.COMPENSATING,StepStatus.COMPENSATION_UNKNOWN,StepStatus.EXECUTING,StepStatus.UNKNOWN}]
            if unresolved:
                return self._report(TransactionStatus.COMPENSATING,
                    "an in-flight compensation exists; after confirming the old worker has stopped, call reconcile()",
                    unresolved[0].id)
            if any(s.status==StepStatus.COMPENSATION_FAILED for s in steps):
                self.engine._transition(self.id,TransactionStatus.PARTIAL_FAILURE,{"reason":"recovered failed compensation"})
                return self._report(TransactionStatus.PARTIAL_FAILURE,"compensation failed; manual remediation may be required")
            failed=next((s for s in steps if s.status==StepStatus.FAILED),None)
            if not failed:
                self.engine._transition(self.id,TransactionStatus.PARTIAL_FAILURE,{"reason":"compensation state has no recorded failed step"})
                return self._report(TransactionStatus.PARTIAL_FAILURE,"cannot safely infer the original failure")
            remaining=[]; unhandled=[]
            for step in reversed(steps):
                if step.status != StepStatus.SUCCEEDED or step.action.effect == EffectClass.READ_ONLY:
                    continue
                adapter=self.engine.adapter_for(step.action)
                if adapter.can_compensate(step.action): remaining.append(step)
                else: unhandled.append(step)
            if remaining:
                return self._run_compensation(failed,remaining,unhandled,ActionExecutionError("resuming compensation"))
            if unhandled:
                self.engine._transition(self.id,TransactionStatus.PARTIAL_FAILURE,{"reason":"effects cannot be compensated"})
                return self._report(TransactionStatus.PARTIAL_FAILURE,"some side effects cannot be compensated")
            self.engine._transition(self.id,TransactionStatus.COMPENSATED)
            return self._report(TransactionStatus.COMPENSATED,"compensation recovered")
        return self.commit()

    def verify(self):
        tx=self.record; chain_ok,chain_msg=self.engine.store.verify_event_chain(self.id)
        checks={"event_chain":chain_ok,"policy_binding":tx.policy_version in {self.engine.policy.identity,self.engine.policy.legacy_identity}}; messages=[chain_msg]
        if not checks["policy_binding"]: messages.append("transaction policy configuration differs from the current engine policy")
        status_events=[e for e in self.engine.store.get_events(self.id) if e.event_type=="TRANSACTION_STATUS_CHANGED"]
        if status_events:
            checks["transaction_status_audit"] = status_events[-1].payload.get("to") == tx.status.value
        else:
            checks["transaction_status_audit"] = tx.status == TransactionStatus.CREATED
        if not checks["transaction_status_audit"]:
            messages.append("transaction status does not match latest audit event")
        added_events=[e for e in self.engine.store.get_events(self.id) if e.event_type=="STEP_ADDED"]
        actual_steps={step.id:step for step in self.steps}
        checks["step_identity_audit"] = len(added_events)==len(actual_steps) and all(
            (e.step_id in actual_steps and e.payload.get("sequence")==actual_steps[e.step_id].sequence
             and e.payload.get("kind")==actual_steps[e.step_id].action.kind) for e in added_events)
        if not checks["step_identity_audit"]:
            messages.append("step identity does not match original audit events")
        if tx.plan_hash:
            plan_ok=self.engine._plan_valid(tx,self.steps)
            checks["plan_hash"]=plan_ok
            if not plan_ok: messages.append("plan hash mismatch")
        # Current data must match its latest digest. Earlier preflight snapshots remain
        # protected by the event chain and can legitimately differ after recovery.
        step_map={step.id:step for step in self.steps}
        digest_ok=True
        latest_digests={}
        for event in self.engine.store.get_events(self.id):
            step=step_map.get(event.step_id)
            if step is None: continue
            for key, attr in (("preflight_sha256","preflight"),("result_sha256","result"),
                              ("compensation_sha256","compensation")):
                if key in event.payload:
                    latest_digests[(step.id,attr)]=event.payload[key]
        for (stepid,attr),expected in latest_digests.items():
            value=getattr(step_map[stepid],attr)
            actual=hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest() if value is not None else None
            if actual != expected: digest_ok=False
        checks["step_audit_digests"]=digest_ok
        if not digest_ok: messages.append("step preflight/result/compensation audit digest mismatch")
        expected_steps={s.id:StepStatus.PENDING.value for s in self.steps}
        expected_attempts={}
        for event in self.engine.store.get_events(self.id):
            if event.event_type == "STEP_EXECUTION_STARTED":
                expected_steps[event.step_id]=StepStatus.EXECUTING.value
            elif event.event_type == "COMPENSATION_STARTED":
                expected_steps[event.step_id]=StepStatus.COMPENSATING.value
            elif "status" in event.payload and event.step_id and "attempt_id" not in event.payload:
                expected_steps[event.step_id]=event.payload["status"]
            if event.event_type == "ATTEMPT_STARTED":
                expected_attempts[event.payload["attempt_id"]]="STARTED"
            elif event.event_type in {"ATTEMPT_FINISHED","ATTEMPT_RECONCILED"}:
                expected_attempts[event.payload["attempt_id"]]=event.payload["status"]
        checks["step_status_audit"]=all(expected_steps[s.id]==s.status.value for s in self.steps)
        actual_attempts={row["id"]:row["status"] for row in self.engine.store.get_attempts(self.id)}
        checks["attempt_status_audit"]=actual_attempts==expected_attempts
        if not checks["step_status_audit"]: messages.append("step status does not match latest audit event")
        if not checks["attempt_status_audit"]: messages.append("attempt status does not match audit history")
        if tx.plan_hash and self.engine._plan_hash(tx,self.steps,legacy=True)==tx.plan_hash:
            messages.append("legacy v0.2 plan: preflight was not included in the plan hash")
        approval=self.engine.store.get_active_approval(self.id)
        if tx.status in {TransactionStatus.APPROVED,TransactionStatus.COMMITTING,TransactionStatus.COMMITTED}:
            checks["approval_binding"]=(not self.engine.policy.approval_required([s.action for s in self.steps]) or bool(approval and approval.plan_hash==tx.plan_hash))
        return VerificationReport(self.id,all(checks.values()),checks,messages)

    def _report(self,status,message=None,failed_step_id=None):
        steps=self.steps
        completed=sum(s.status in {StepStatus.SUCCEEDED,StepStatus.COMPENSATED} for s in steps)
        return CommitReport(self.id,status,completed,len(steps),failed_step_id,message)
