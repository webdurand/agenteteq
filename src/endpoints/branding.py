"""REST API endpoints for brand profile management."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from src.auth.deps import get_current_user, require_active_plan
from src.models.branding import (
    create_brand_profile,
    update_brand_profile,
    delete_brand_profile,
    list_brand_profiles,
    get_default_brand_profile,
)
from src.models.style_references import (
    create_style_reference,
    list_style_references,
    delete_style_reference,
)
from src.integrations.image_storage import upload_user_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/branding", tags=["branding"])


class BrandProfileCreate(BaseModel):
    name: str
    is_default: bool = False
    primary_color: Optional[str] = "#1A1A2E"
    secondary_color: Optional[str] = "#16213E"
    accent_color: Optional[str] = "#E94560"
    bg_color: Optional[str] = "#0F0F0F"
    text_primary_color: Optional[str] = "#FFFFFF"
    text_secondary_color: Optional[str] = "#D0D0D0"
    font_heading: Optional[str] = "Inter Bold"
    font_body: Optional[str] = "Inter"
    logo_url: Optional[str] = ""
    style_description: Optional[str] = ""
    tone_of_voice: Optional[str] = ""
    target_audience: Optional[str] = ""


class BrandProfileUpdate(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    bg_color: Optional[str] = None
    text_primary_color: Optional[str] = None
    text_secondary_color: Optional[str] = None
    font_heading: Optional[str] = None
    font_body: Optional[str] = None
    logo_url: Optional[str] = None
    style_description: Optional[str] = None
    tone_of_voice: Optional[str] = None
    target_audience: Optional[str] = None


@router.get("")
async def api_list_brand_profiles(user=Depends(get_current_user)):
    profiles = list_brand_profiles(user["phone_number"])
    return {"profiles": profiles}


@router.post("")
async def api_create_brand_profile(body: BrandProfileCreate, user=Depends(require_active_plan)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Nome da marca e obrigatorio.")

    profile = create_brand_profile(
        user_id=user["phone_number"],
        name=body.name.strip(),
        is_default=body.is_default,
        primary_color=body.primary_color,
        secondary_color=body.secondary_color,
        accent_color=body.accent_color,
        bg_color=body.bg_color,
        text_primary_color=body.text_primary_color,
        text_secondary_color=body.text_secondary_color,
        font_heading=body.font_heading,
        font_body=body.font_body,
        logo_url=body.logo_url,
        style_description=body.style_description,
        tone_of_voice=body.tone_of_voice,
        target_audience=body.target_audience,
    )
    return {"profile": profile}


@router.put("/{profile_id}")
async def api_update_brand_profile(
    profile_id: int,
    body: BrandProfileUpdate,
    user=Depends(get_current_user),
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar.")

    profile = update_brand_profile(profile_id, user["phone_number"], **updates)
    if not profile:
        raise HTTPException(status_code=404, detail="Perfil de marca nao encontrado.")
    return {"profile": profile}


@router.delete("/{profile_id}")
async def api_delete_brand_profile(profile_id: int, user=Depends(get_current_user)):
    ok = delete_brand_profile(profile_id, user["phone_number"])
    if not ok:
        raise HTTPException(status_code=404, detail="Perfil de marca nao encontrado.")
    return {"ok": True}


@router.get("/default")
async def api_get_default_brand_profile(user=Depends(get_current_user)):
    profile = get_default_brand_profile(user["phone_number"])
    return {"profile": profile}


@router.post("/upload-logo")
async def api_upload_brand_logo(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Upload a brand logo image to Cloudinary and return the URL."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="O arquivo precisa ser uma imagem.")

    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem muito grande. Maximo 5MB.")

    try:
        url = upload_user_image(user["phone_number"], contents)
        return {"url": url}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Erro ao fazer upload do logo: %s", e)
        raise HTTPException(status_code=500, detail="Erro ao fazer upload da imagem.")


# ──────────────────────────── Style References ────────────────────────────


class StyleReferenceCreate(BaseModel):
    image_url: str
    title: Optional[str] = ""
    source_url: Optional[str] = ""
    brand_profile_id: Optional[int] = None
    extracted_colors: Optional[dict] = None
    style_description: Optional[str] = ""
    tags: Optional[str] = ""


@router.get("/references")
async def api_list_style_references(
    brand_profile_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
    user=Depends(get_current_user),
):
    refs = list_style_references(
        user["phone_number"],
        brand_profile_id=brand_profile_id,
        limit=limit,
        offset=offset,
    )
    return {"references": refs}


@router.post("/references")
async def api_create_style_reference(
    body: StyleReferenceCreate,
    user=Depends(get_current_user),
):
    if not body.image_url.strip():
        raise HTTPException(status_code=400, detail="URL da imagem e obrigatoria.")
    ref = create_style_reference(
        user_id=user["phone_number"],
        image_url=body.image_url.strip(),
        title=body.title,
        source_url=body.source_url,
        brand_profile_id=body.brand_profile_id,
        extracted_colors=body.extracted_colors,
        style_description=body.style_description,
        tags=body.tags,
    )
    return {"reference": ref}


@router.delete("/references/{ref_id}")
async def api_delete_style_reference(ref_id: int, user=Depends(get_current_user)):
    ok = delete_style_reference(ref_id, user["phone_number"])
    if not ok:
        raise HTTPException(status_code=404, detail="Referencia nao encontrada.")
    return {"ok": True}


@router.post("/references/upload")
async def api_upload_style_reference_image(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Upload a style reference image to Cloudinary and return the URL."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="O arquivo precisa ser uma imagem.")
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem muito grande. Maximo 5MB.")
    try:
        url = upload_user_image(user["phone_number"], contents)
        return {"url": url}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Erro ao fazer upload de referencia: %s", e)
        raise HTTPException(status_code=500, detail="Erro ao fazer upload da imagem.")
