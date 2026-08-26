from typing import Literal

from pydantic import BaseModel, Field

ExecutionResult = Literal["success", "failed", "skipped"]


class ActionResult(BaseModel):
    executed: bool
    result: ExecutionResult
    revenue_recovered: int = Field(ge=0, description="Minor units (paise)")
    message: str = Field(max_length=300)
    gateway: str  # "simulator" | "razorpay_test_mode"
