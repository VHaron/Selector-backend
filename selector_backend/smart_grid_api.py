from fastapi import APIRouter, HTTPException
from .models import SmartGridRequest
from .state import get_user
from .smart_grid import optimize_smart_grid

router = APIRouter()


@router.post("/api/smart_grid/build")
def build_smart_grid(req: SmartGridRequest):
    # Validate the active server-side login session before running any computation.
    get_user(req.session_token)
    try:
        result = optimize_smart_grid({
            "tournaments": req.tournaments,
            "locked": req.locked,
            "settings": req.settings.model_dump(),
        })
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Не удалось рассчитать умную сетку")
