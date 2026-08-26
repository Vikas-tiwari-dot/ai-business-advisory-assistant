from typing import Literal

from pydantic import BaseModel, Field

CustomerSegment = Literal["new", "standard", "high_value"]


class CustomerContext(BaseModel):
    customer_segment: CustomerSegment
    lifetime_value: int = Field(ge=0, description="Minor units (paise)")
    failure_rate: float = Field(ge=0.0, le=1.0)
    recovery_attempts: int = Field(ge=0, description="Recovery attempts made on the current open case")
