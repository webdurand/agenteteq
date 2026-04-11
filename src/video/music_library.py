"""
Royalty-free music catalog for video generation.
Categories mapped to moods/templates for auto-selection.
"""

import random
from typing import Optional

# Each entry: {"url": str, "title": str, "bpm": int, "duration_s": int}
# URLs should point to royalty-free sources (Pixabay, Uppbeat, etc.)
# Populate with actual URLs when sourcing music files
CATALOG: dict[str, list[dict]] = {
    "energetic": [
        {"title": "Drive Forward", "bpm": 128, "duration_s": 120, "url": ""},
        {"title": "Upbeat Energy", "bpm": 135, "duration_s": 90, "url": ""},
    ],
    "calm": [
        {"title": "Gentle Morning", "bpm": 85, "duration_s": 180, "url": ""},
        {"title": "Soft Focus", "bpm": 72, "duration_s": 150, "url": ""},
    ],
    "inspirational": [
        {"title": "Rise Up", "bpm": 110, "duration_s": 120, "url": ""},
        {"title": "New Horizons", "bpm": 100, "duration_s": 90, "url": ""},
    ],
    "tech": [
        {"title": "Digital Flow", "bpm": 120, "duration_s": 120, "url": ""},
        {"title": "Cyber Pulse", "bpm": 130, "duration_s": 90, "url": ""},
    ],
    "dramatic": [
        {"title": "Epic Reveal", "bpm": 95, "duration_s": 120, "url": ""},
        {"title": "Dark Cinematic", "bpm": 80, "duration_s": 150, "url": ""},
    ],
    "fun": [
        {"title": "Happy Vibes", "bpm": 118, "duration_s": 90, "url": ""},
        {"title": "Playful Beat", "bpm": 125, "duration_s": 60, "url": ""},
    ],
}

# Template -> suggested music category
TEMPLATE_MOOD_MAP: dict[str, str] = {
    "tutorial": "calm",
    "storytelling": "inspirational",
    "listicle": "energetic",
    "transformation": "dramatic",
    "qa": "tech",
    "behind_the_scenes": "fun",
    "pov": "dramatic",
    "myth_busting": "tech",
    "hot_take": "energetic",
}


def get_categories() -> list[str]:
    """Return available music categories."""
    return list(CATALOG.keys())


def get_music(category: str) -> Optional[dict]:
    """Get a random track from a category. Returns None if category empty or URLs not set."""
    tracks = CATALOG.get(category, [])
    available = [t for t in tracks if t.get("url")]
    if not available:
        return None
    return random.choice(available)


def suggest_music_for_template(template_id: str) -> Optional[dict]:
    """Auto-suggest music based on video template."""
    category = TEMPLATE_MOOD_MAP.get(template_id, "inspirational")
    return get_music(category)


def list_catalog() -> list[dict]:
    """Return full catalog for display."""
    result = []
    for category, tracks in CATALOG.items():
        for track in tracks:
            result.append({
                "category": category,
                "title": track["title"],
                "bpm": track["bpm"],
                "duration_s": track["duration_s"],
                "available": bool(track.get("url")),
            })
    return result
