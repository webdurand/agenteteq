from enum import Enum
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

class SubscriptionStatus(str, Enum):
    FREE = "free"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    INCOMPLETE = "incomplete"

@dataclass
class BillingContext:
    status: SubscriptionStatus
    trial_end: Optional[datetime]
    current_period_end: Optional[datetime]
    cancel_at_period_end: bool
    plan_code: Optional[str]
    has_active_subscription: bool
    has_stripe_subscription: bool = False
