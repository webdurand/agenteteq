"""Curated Google Font pairings for carousel and branding."""

FONT_PAIRINGS = [
    {"heading": "Space Grotesk", "body": "Inter", "mood": "tech, moderno"},
    {"heading": "Outfit", "body": "Inter", "mood": "clean, startup"},
    {"heading": "Sora", "body": "Plus Jakarta Sans", "mood": "futurista, elegante"},
    {"heading": "Playfair Display", "body": "Source Sans 3", "mood": "editorial, sofisticado"},
    {"heading": "Bebas Neue", "body": "Open Sans", "mood": "bold, impactante"},
    {"heading": "Montserrat", "body": "Lora", "mood": "classico, profissional"},
    {"heading": "Poppins", "body": "Nunito", "mood": "amigavel, criativo"},
    {"heading": "Raleway", "body": "Roboto", "mood": "minimalista, corporate"},
    {"heading": "DM Serif Display", "body": "DM Sans", "mood": "editorial, premium"},
    {"heading": "Archivo Black", "body": "Work Sans", "mood": "ousado, streetwear"},
    {"heading": "Cormorant Garamond", "body": "Proza Libre", "mood": "luxo, fashion"},
    {"heading": "Oswald", "body": "Quattrocento Sans", "mood": "impactante, noticioso"},
]


def suggest_pairings(mood: str = "", limit: int = 3) -> list[dict]:
    """Suggest font pairings based on mood/style keywords."""
    if not mood:
        return FONT_PAIRINGS[:limit]

    mood_lower = mood.lower()
    scored = []
    for pair in FONT_PAIRINGS:
        score = sum(1 for kw in pair["mood"].split(", ") if kw in mood_lower)
        scored.append((score, pair))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [pair for _, pair in scored[:limit]]


def get_pairing_by_heading(heading_font: str) -> dict | None:
    """Find a pairing that uses the given heading font."""
    heading_lower = heading_font.lower()
    for pair in FONT_PAIRINGS:
        if pair["heading"].lower() == heading_lower:
            return pair
    return None
