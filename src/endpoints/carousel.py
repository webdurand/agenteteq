from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Dict, Any
from src.auth.deps import get_current_user
from src.models.carousel import list_user_carousels, get_carousel

router = APIRouter(prefix="/carousel", tags=["Carousel"])

@router.get("/", response_model=List[Dict[str, Any]])
def get_user_carousels(user: dict = Depends(get_current_user)):
    """
    Retorna a lista de carrosséis do usuário logado (ordem decrescente de criação).
    """
    user_id = user.get("phone_number")
    if not user_id:
        raise HTTPException(status_code=400, detail="Usuário sem identificador")
        
    carousels = list_user_carousels(user_id)
    return carousels

@router.get("/{carousel_id}", response_model=Dict[str, Any])
def get_carousel_by_id(carousel_id: str, user: dict = Depends(get_current_user)):
    """
    Retorna os detalhes de um carrossel específico, incluindo todos os slides gerados.
    """
    user_id = user.get("phone_number")
    
    carousel = get_carousel(carousel_id)
    if not carousel:
        raise HTTPException(status_code=404, detail="Carrossel não encontrado")
        
    if carousel.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Acesso negado a este carrossel")
        
    return carousel
