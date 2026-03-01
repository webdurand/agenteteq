import httpx
from urllib.parse import quote


def get_weather(city: str) -> str:
    """
    Retorna a previsao do tempo atual e para os proximos 2 dias para a cidade informada.
    Usa wttr.in (gratuito, sem API key necessaria).
    
    Args:
        city: Nome da cidade (ex: 'Sao Paulo', 'Rio de Janeiro', 'Curitiba').
    
    Returns:
        String formatada com temperatura, condicao do tempo e previsao.
    """
    print(f"[WEATHER] Buscando previsao do tempo para: {city}")
    try:
        city_encoded = quote(city)
        url = f"https://wttr.in/{city_encoded}?format=j1"
        print(f"[WEATHER] URL: {url}")
        response = httpx.get(url, timeout=15, follow_redirects=True)
        response.raise_for_status()
        data = response.json()

        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        feels_like = current["FeelsLikeC"]
        humidity = current["humidity"]
        description = _get_description(current.get("weatherDesc", [{}])[0].get("value", ""))
        wind_kmph = current["windspeedKmph"]

        lines = [f"{city} — agora: {temp_c}°C (sensacao {feels_like}°C), {description}, umidade {humidity}%, vento {wind_kmph} km/h"]

        weather_days = data.get("weather", [])
        day_labels = ["hoje", "amanha", "depois de amanha"]
        for i, day in enumerate(weather_days[:3]):
            if i == 0:
                continue
            max_c = day.get("maxtempC", "?")
            min_c = day.get("mintempC", "?")
            day_desc = _get_description(
                day.get("hourly", [{}])[4].get("weatherDesc", [{}])[0].get("value", "") if day.get("hourly") else ""
            )
            lines.append(f"{day_labels[i]}: min {min_c}°C / max {max_c}°C — {day_desc}")

        result = "\n".join(lines)
        print(f"[WEATHER] Resultado: {result[:100]}...")
        return result

    except httpx.TimeoutException:
        msg = f"Nao consegui pegar a previsao do tempo pra {city} agora (timeout). Tenta de novo em alguns minutos."
        print(f"[WEATHER] Timeout para {city}")
        return msg
    except httpx.HTTPStatusError as e:
        msg = f"Erro ao buscar previsao do tempo pra {city}: status {e.response.status_code}."
        print(f"[WEATHER] HTTP error {e.response.status_code} para {city}")
        return msg
    except Exception as e:
        msg = f"Nao consegui buscar o tempo pra {city}: {e}"
        print(f"[WEATHER] Erro inesperado para {city}: {e}")
        return msg


_WEATHER_TRANSLATIONS: dict[str, str] = {
    "Sunny": "ensolarado",
    "Clear": "ceu limpo",
    "Partly cloudy": "parcialmente nublado",
    "Cloudy": "nublado",
    "Overcast": "encoberto",
    "Mist": "neblina",
    "Fog": "nevoeiro",
    "Light rain": "chuva fraca",
    "Moderate rain": "chuva moderada",
    "Heavy rain": "chuva forte",
    "Light drizzle": "garoa leve",
    "Drizzle": "garoa",
    "Thundery outbreaks possible": "possibilidade de trovoadas",
    "Patchy rain possible": "chuva isolada possivel",
    "Blizzard": "nevasca",
    "Snow": "neve",
    "Light snow": "neve leve",
}


def _get_description(value: str) -> str:
    return _WEATHER_TRANSLATIONS.get(value, value.lower() if value else "sem informacao")
