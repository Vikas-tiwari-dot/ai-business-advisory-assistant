from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.audit import EvaluationResult
from app.schemas.api_responses import EvaluationResponse

router = APIRouter(tags=["evaluation"])


@router.get("/evaluation", response_model=EvaluationResponse)
def get_latest_evaluation(db: Session = Depends(get_db)) -> EvaluationResponse:
    latest = db.query(EvaluationResult).order_by(EvaluationResult.created_at.desc()).first()
    if latest is None:
        return EvaluationResponse(run_id=None, dataset_split=None, metrics=None, created_at=None, available=False)
    return EvaluationResponse(
        run_id=latest.run_id,
        dataset_split=latest.dataset_split,
        metrics=latest.metrics_json,
        created_at=latest.created_at,
        available=True,
    )
