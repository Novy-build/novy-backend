from fastapi import APIRouter, Response

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Check whether the API process can respond")
async def liveness(response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"status": "alive"}
