import enum


class CustomerSegment(str, enum.Enum):
    NEW = "new"
    STANDARD = "standard"
    HIGH_VALUE = "high_value"


class PaymentMethod(str, enum.Enum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMI = "emi"


class PaymentStatus(str, enum.Enum):
    CREATED = "created"
    FAILED = "failed"
    RECOVERED = "recovered"
    UNRECOVERABLE = "unrecoverable"
    PENDING = "pending"


class EventSource(str, enum.Enum):
    WEBHOOK = "webhook"
    CSV = "csv"
    SIMULATOR = "simulator"


class DatasetSplit(str, enum.Enum):
    TRAIN = "train"
    HOLDOUT = "holdout"


class AttemptStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"


class RecoveryCaseStatus(str, enum.Enum):
    OPEN = "open"
    RECOVERED = "recovered"
    STOPPED = "stopped"
    ESCALATED = "escalated"
    CLOSED_UNRECOVERED = "closed_unrecovered"


class AIStage(str, enum.Enum):
    DIAGNOSIS = "diagnosis"
    ACTION_PROPOSAL = "action_proposal"


class DiagnosisCategory(str, enum.Enum):
    TEMPORARY_FAILURE = "temporary_failure"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    BANK_DECLINE = "bank_decline"
    EXPIRED_INSTRUMENT = "expired_instrument"
    REPEATED_FAILURE = "repeated_failure"
    CHECKOUT_ABANDONMENT = "checkout_abandonment"
    OVERDUE_INVOICE = "overdue_invoice"
    UNKNOWN = "unknown"


class RecoveryActionType(str, enum.Enum):
    RETRY_PAYMENT = "RETRY_PAYMENT"
    SEND_REMINDER = "SEND_REMINDER"
    OFFER_ALTERNATE_METHOD = "OFFER_ALTERNATE_METHOD"
    SCHEDULE_RETRY = "SCHEDULE_RETRY"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    STOP = "STOP"


class ExecutionResult(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class HumanOverride(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"
    STOP = "stop"


class AuditStage(str, enum.Enum):
    PAYMENT_FAILED = "PAYMENT_FAILED"
    RISK_DETECTED = "RISK_DETECTED"
    AI_DIAGNOSED = "AI_DIAGNOSED"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    POLICY_APPROVED = "POLICY_APPROVED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    PAYMENT_RECOVERED = "PAYMENT_RECOVERED"
    ESCALATED = "ESCALATED"
    HUMAN_DECISION = "HUMAN_DECISION"
    AI_FALLBACK_USED = "AI_FALLBACK_USED"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    ERROR = "ERROR"
