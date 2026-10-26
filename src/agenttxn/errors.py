class AgentTxnError(Exception): pass
class NotFoundError(AgentTxnError): pass
class DuplicateIdempotencyKeyError(AgentTxnError): pass
class InvalidTransitionError(AgentTxnError): pass
class PlanFrozenError(AgentTxnError): pass
class PolicyDeniedError(AgentTxnError): pass
class ApprovalRequiredError(AgentTxnError): pass
class ApprovalStaleError(AgentTxnError): pass
class AdapterNotFoundError(AgentTxnError): pass
class PreflightError(AgentTxnError): pass
class ActionExecutionError(AgentTxnError): pass
class OutcomeUnknownError(AgentTxnError): pass
class ReconciliationRequiredError(AgentTxnError): pass
class CompensationError(AgentTxnError): pass
class IntegrityVerificationError(AgentTxnError): pass
class ConcurrentModificationError(AgentTxnError): pass

class PolicyVersionMismatchError(AgentTxnError): pass
