from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Dict, Any
from src.auth.deps import get_current_user, require_active_plan
from src.models.carousel import list_user_carousels, get_carousel, delete_carousel

router = APIRouter(prefix="/carousel", tags=["Carousel"])

@router.get("/")
def get_user_carousels(
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user)
):
    user_id = user.get("phone_number")
    if not user_id:
        raise HTTPException(status_code=400, detail="Usuário sem identificador")
        
    return list_user_carousels(user_id, limit=limit, offset=offset)

@router.get("/{carousel_id}", response_model=Dict[str, Any])
def get_carousel_by_id(carousel_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("phone_number")
    
    carousel = get_carousel(carousel_id)
    if not carousel:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado")
        
    if carousel.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Acesso negado a este carrossel")
        
    return carousel

@router.post("/{carousel_id}/cancel")
async def cancel_carousel_generation(carousel_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("phone_number")
    if not user_id:
        raise HTTPException(status_code=400, detail="Usuário sem identificador")

    carousel = get_carousel(carousel_id)
    if not carousel:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado")
    if carousel.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Acesso negado a este carrossel")
    if carousel.get("status") not in ("generating",):
        raise HTTPException(status_code=400, detail="Carrossel não está em geração")

    from src.queue.task_queue import cancel_task_by_carousel
    cancel_task_by_carousel(user_id, carousel_id)

    # Update DB chat message placeholder to FAILED so reconcile picks it up
    try:
        import asyncio
        from src.models.chat_messages import update_message_by_prefix
        await asyncio.to_thread(
            update_message_by_prefix, user_id,
            "__CAROUSEL_GENERATING__",
            f"__CAROUSEL_FAILED__{carousel_id}",
        )
    except Exception:
        pass

    # Send WS event for instant frontend feedback
    try:
        from src.endpoints.web import ws_manager
        await ws_manager.send_personal_message(user_id, {
            "type": "carousel_failed",
            "carousel_id": carousel_id,
            "cancelled": True,
        })
    except Exception:
        pass

    return {"status": "cancelled"}


@router.delete("/{carousel_id}")
def delete_carousel_by_id(carousel_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("phone_number")
    if not user_id:
        raise HTTPException(status_code=400, detail="Usuário sem identificador")
    
    deleted = delete_carousel(carousel_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado")
    
    return {"status": "deleted"}
