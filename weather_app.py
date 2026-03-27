import requests
import time
import os
import json
import hashlib
from datetime import datetime
from collections import defaultdict, Counter
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("OW_API_KEY")
CACHE_DIR = "./.cache"

# Создаем директорию для кэша, если она отсутствует
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)


def get_cache_path(url, params):
    """Генерирует путь к файлу кэша на основе URL и параметров."""
    key = hashlib.md5(f"{url}{params}".encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.json")


def request_with_retry(url, params):
    """
    Выполняет запрос с кэшированием на 10 минут и 3 попытками 
    при получении ошибки 429 (лимит запросов).
    """
    cache_path = get_cache_path(url, params)
    if os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < 600:
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass

    # Ретраи с паузами 1, 2 и 4 секунды
    for delay in [1, 2, 4]:
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 429:
                time.sleep(delay)
                continue
            response.raise_for_status()
            data = response.json()
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            return data
        except:
            continue
    return None


def get_coordinates(city: str, limit: int = 1):
    """Использование URL: https://api.openweathermap.org/geo/1.0/direct"""
    url = "https://api.openweathermap.org/geo/1.0/direct"
    params = {"q": city, "limit": limit, "appid": API_KEY}
    data = request_with_retry(url, params)
    if data and isinstance(data, list) and len(data) > 0:
        # Извлекаем lat и lon из первого объекта в списке
        return float(data[0]['lat']), float(data[0]['lon'])
    return None


def geocode_address(query: str):
    """
    Геокодирование произвольной строки адреса (город, улица, дом)
    через Nominatim (OpenStreetMap). Возвращает (lat, lon, full_address)
    либо None, если адрес не найден.
    """
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": "WeatherTelegramBot/1.0"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        item = data[0]
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
        full_address = item.get("display_name", query)
        return lat, lon, full_address
    except Exception:
        return None


def reverse_geocode(lat: float, lon: float):
    """Обратное геокодирование координат в человекочитаемый адрес."""
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "zoom": 18,
        "addressdetails": 1,
    }
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": "WeatherTelegramBot/1.0"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("display_name", f"{lat:.4f}, {lon:.4f}")
    except Exception:
        return f"{lat:.4f}, {lon:.4f}"


def get_current_weather(lat: float, lon: float):
    """Использование URL: https://api.openweathermap.org/data/2.5/weather"""
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": API_KEY,
        "units": "metric",
        "lang": "ru",
    }
    return request_with_retry(url, params)


def get_forecast_5d3h(lat: float, lon: float):
    """Запрос прогноза на 5 дней по URL OpenWeather."""
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": API_KEY,
        "units": "metric",
        "lang": "ru",
    }
    data = request_with_retry(url, params)
    return data.get('list', []) if data else []


def summarize_forecast_by_days(forecast_list, limit_days: int = 5):
    """
    Группирует почасовой прогноз по датам и считает для каждого дня
    минимальную / максимальную температуру и наиболее частое описание.
    Возвращает список словарей c ключами: date, min_temp, max_temp, description.
    """
    if not forecast_list:
        return []

    by_date = defaultdict(list)
    for item in forecast_list:
        dt_txt = item.get("dt_txt")
        if not dt_txt:
            # Для надежности пробуем использовать dt, если нет dt_txt
            ts = item.get("dt")
            if ts:
                dt_txt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            else:
                continue
        date_key = dt_txt[:10]
        by_date[date_key].append(item)

    result = []
    for date_key in sorted(by_date.keys())[:limit_days]:
        items = by_date[date_key]
        temps = [i.get("main", {}).get("temp") for i in items if i.get("main", {}).get("temp") is not None]
        if not temps:
            continue
        min_temp = min(temps)
        max_temp = max(temps)
        descs = [
            i.get("weather", [{}])[0].get("description", "").capitalize()
            for i in items
            if i.get("weather")
        ]
        common_desc = ""
        if descs:
            common_desc = Counter(descs).most_common(1)[0][0]

        result.append(
            {
                "date": date_key,
                "min_temp": min_temp,
                "max_temp": max_temp,
                "description": common_desc,
            }
        )

    return result


def format_daily_forecast_summary(place_name: str, forecast_list, limit_days: int = 5):
    """
    Формирует краткий текстовый прогноз по дням на 5 дней вперёд.
    Используется ботом для первого сообщения «краткий прогноз».
    """
    daily = summarize_forecast_by_days(forecast_list, limit_days=limit_days)
    if not daily:
        return "❌ Не удалось получить прогноз на 5 дней."

    lines = [f"📅 **Краткий прогноз для {place_name}:**\n"]
    for d in daily:
        # Дата в удобном формате ДД.ММ
        try:
            dt_obj = datetime.strptime(d["date"], "%Y-%m-%d")
            date_human = dt_obj.strftime("%d.%m (%a)")
        except Exception:
            date_human = d["date"]

        lines.append(
            f"- {date_human}: от {d['min_temp']:.0f}°C до {d['max_temp']:.0f}°C, {d['description']}"
        )
    lines.append("\nНажмите кнопку дня, чтобы посмотреть детальный почасовой прогноз.")
    return "\n".join(lines)


def format_single_day_details(place_name: str, forecast_list, date_str: str):
    """
    Детальный прогноз на выбранный день (все 3-часовые интервалы).
    date_str ожидается в формате 'YYYY-MM-DD'.
    """
    if not forecast_list:
        return "❌ Данные прогноза временно недоступны."

    slots = [
        item for item in forecast_list
        if str(item.get("dt_txt", "")).startswith(date_str)
    ]
    if not slots:
        return "❌ Для выбранного дня нет данных прогноза."

    try:
        dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
        date_human = dt_obj.strftime("%d.%m.%Y (%A)")
    except Exception:
        date_human = date_str

    lines = [f"📆 **Детальный прогноз для {place_name} на {date_human}:**\n"]
    for item in sorted(slots, key=lambda x: x.get("dt", 0)):
        dt_txt = item.get("dt_txt", "")
        time_part = dt_txt[11:16] if len(dt_txt) >= 16 else "??:??"
        main = item.get("main", {})
        temp = main.get("temp")
        feels = main.get("feels_like")
        desc = ""
        weather_arr = item.get("weather") or []
        if weather_arr:
            desc = weather_arr[0].get("description", "")
        temp_txt = f"{temp}°C" if temp is not None else "нет данных"
        feels_txt = f"{feels}°C" if feels is not None else "нет данных"
        lines.append(
            f"{time_part}: {temp_txt} (ощущается {feels_txt}), {desc}"
        )

    return "\n".join(lines)


def get_air_pollution(lat: float, lon: float):
    """Использование URL: https://api.openweathermap.org/data/2.5/air_pollution"""
    url = "https://api.openweathermap.org/data/2.5/air_pollution"
    params = {"lat": lat, "lon": lon, "appid": API_KEY}
    data = request_with_retry(url, params)
    if data and 'list' in data and len(data['list']) > 0:
        return data['list'][0].get('components', {})
    return {}


def analyze_air_pollution(components: dict):
    """
    Оценка качества воздуха по содержанию PM2.5 с расшифровкой
    по европейской шкале Air Quality Index (AQI Europe).

    Диапазоны категорий для PM2.5 (мкг/м³), согласно таблице AQI Europe:
    - 0–15   → Очень низкое загрязнение (Very Low)
    - 15–30  → Низкое загрязнение (Low)
    - 30–55  → Среднее загрязнение (Medium)
    - 55–110 → Высокое загрязнение (High)
    - >110   → Очень высокое загрязнение (Very High)
    """
    pm25 = float(components.get("pm2_5", 0) or 0)

    if pm25 <= 15:
        status = "Очень низкое 🟢"
        explanation = (
            "Качество воздуха по PM2.5: очень низкое загрязнение. "
            "Воздух чистый, риски для здоровья минимальны."
        )
    elif pm25 <= 30:
        status = "Низкое 🟢🟡"
        explanation = (
            "Качество воздуха по PM2.5: низкое загрязнение. "
            "Уязвимые группы могут слегка реагировать, большинству людей ничего не грозит."
        )
    elif pm25 <= 55:
        status = "Среднее 🟡"
        explanation = (
            "Качество воздуха по PM2.5: среднее загрязнение. "
            "Чувствительным людям стоит сократить длительные нагрузки на улице."
        )
    elif pm25 <= 110:
        status = "Высокое 🟠"
        explanation = (
            "Качество воздуха по PM2.5: высокое загрязнение. "
            "Рекомендуется ограничить длительное пребывание на улице, особенно детям и людям с заболеваниями дыхательной системы."
        )
    else:
        status = "Очень высокое 🔴"
        explanation = (
            "Качество воздуха по PM2.5: очень высокое загрязнение. "
            "По возможности оставайтесь в помещении и избегайте интенсивных нагрузок на улице."
        )

    return {
        "status": status,
        "explanation": explanation,
        "pm2_5": pm25,
    }


def get_uv_index(lat: float, lon: float):
    """
    Получение текущего индекса УФ по координатам.

    Сначала пробуем One Call API OpenWeather, а если данных нет (или ключ не даёт
    доступа), используем бесплатный сервис Open-Meteo как резервный источник.
    """
    # Попытка через OpenWeather One Call
    try:
        url = "https://api.openweathermap.org/data/2.5/onecall"
        params = {
            "lat": lat,
            "lon": lon,
            "appid": API_KEY,
            "exclude": "minutely,hourly,daily,alerts",
        }
        data = request_with_retry(url, params)
        if data and "current" in data and "uvi" in data["current"]:
            try:
                return float(data["current"]["uvi"])
            except (TypeError, ValueError):
                pass
    except Exception:
        pass

    # Резерв: Open-Meteo (без ключа, только UV index)
    try:
        om_url = "https://api.open-meteo.com/v1/forecast"
        om_params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "uv_index",
            "current": "uv_index",
        }
        resp = requests.get(om_url, params=om_params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Пытаемся взять текущий uv_index, затем – ближайший часовой
        current = data.get("current", {})
        cur_val = current.get("uv_index")
        if cur_val is not None:
            return float(cur_val)

        hourly_vals = data.get("hourly", {}).get("uv_index")
        if hourly_vals:
            return float(hourly_vals[0])
    except Exception:
        pass

    return None


def describe_uv_index(uvi: float):
    """Текстовая расшифровка значения УФ-индекса."""
    if uvi is None:
        return "нет данных"
    if uvi <= 2:
        return f"{uvi:.1f} — низкий уровень, защита обычно не требуется."
    if uvi <= 5:
        return f"{uvi:.1f} — умеренный уровень, желательно надеть солнцезащитные очки."
    if uvi <= 7:
        return f"{uvi:.1f} — высокий уровень, рекомендуется крем SPF 30+ и головной убор."
    if uvi <= 10:
        return f"{uvi:.1f} — очень высокий уровень, избегайте долгого пребывания на солнце."
    return f"{uvi:.1f} — экстремальный уровень, по возможности оставайтесь в тени."


def get_full_card(lat: float, lon: float, city_name: str):
    """Сборка детальной карточки погоды с координатами."""
    w = get_current_weather(lat, lon)
    air = get_air_pollution(lat, lon)
    if not w or 'main' not in w:
        return "❌ Ошибка: данные о погоде временно недоступны."

    sunrise = datetime.fromtimestamp(w['sys']['sunrise']).strftime('%H:%M')
    sunset = datetime.fromtimestamp(w['sys']['sunset']).strftime('%H:%M')
    air_info = analyze_air_pollution(air)
    desc = w['weather'][0]['description'].capitalize()
    uvi = get_uv_index(lat, lon)
    uv_text = describe_uv_index(uvi)
    humidity = w['main'].get('humidity')
    wind_speed = w.get('wind', {}).get('speed')

    return (
        f"🌍 **Место: {city_name}**\n"
        f"📍 Координаты: `{lat:.4f}, {lon:.4f}`\n"
        f"🌡 Температура: {w['main']['temp']}°C (ощущается {w['main']['feels_like']}°C)\n"
        f"💧 Влажность: {humidity}%\n"
        f"📉 Давление: {w['main']['pressure']} гПа\n"
        f"☁️ Облачность: {w['clouds']['all']}%\n"
        f"💨 Ветер: {wind_speed} м/с\n"
        f"🌅 Восход: {sunrise} | Закат: {sunset}\n"
        f"🍃 Воздух: {air_info['status']} (PM2.5: {air_info['pm2_5']} мкг/м³)\n"
        f"   {air_info['explanation']}\n"
        f"🔆 УФ-индекс: {uv_text}\n"
        f"📝 Описание: {desc}"
    )


def get_extended_card(lat: float, lon: float, place_name: str):
    """Расширенный отчет с максимальным набором данных и расшифровками."""
    w = get_current_weather(lat, lon)
    air = get_air_pollution(lat, lon)
    if not w or 'main' not in w:
        return "❌ Ошибка: данные о погоде временно недоступны."

    sunrise = datetime.fromtimestamp(w['sys']['sunrise']).strftime('%H:%M')
    sunset = datetime.fromtimestamp(w['sys']['sunset']).strftime('%H:%M')
    air_info = analyze_air_pollution(air)
    uvi = get_uv_index(lat, lon)
    uv_text = describe_uv_index(uvi)
    humidity = w['main'].get('humidity')
    wind = w.get('wind', {})
    wind_speed = wind.get('speed')
    wind_gust = wind.get('gust')
    visibility_km = (
        w.get('visibility', 0) / 1000 if w.get('visibility') is not None else None
    )
    desc = w['weather'][0]['description'].capitalize()

    pollutants_lines = []
    if 'pm2_5' in air:
        pollutants_lines.append(
            f"PM2.5: {air['pm2_5']} мкг/м³ — мелкие частицы, глубоко проникающие в легкие."
        )
    if 'pm10' in air:
        pollutants_lines.append(
            f"PM10: {air['pm10']} мкг/м³ — более крупные частицы пыли в воздухе."
        )
    if 'no2' in air:
        pollutants_lines.append(
            f"NO₂: {air['no2']} мкг/м³ — диоксид азота, показатель выхлопных газов."
        )
    if 'so2' in air:
        pollutants_lines.append(
            f"SO₂: {air['so2']} мкг/м³ — диоксид серы, связан с промышленными выбросами."
        )
    if 'o3' in air:
        pollutants_lines.append(
            f"O₃: {air['o3']} мкг/м³ — приземный озон, может раздражать дыхательные пути."
        )
    if 'co' in air:
        pollutants_lines.append(
            f"CO: {air['co']} мкг/м³ — оксид углерода, показатель сгорания топлива."
        )

    pollutants_text = (
        "\n".join(pollutants_lines)
        if pollutants_lines
        else "Нет детальной информации по загрязнителям."
    )

    vis_part = f"{visibility_km:.1f} км" if visibility_km is not None else "нет данных"
    wind_part = f"{wind_speed} м/с" if wind_speed is not None else "нет данных"
    if wind_gust is not None:
        wind_part += f" (порывы до {wind_gust} м/с)"

    return (
        f"📍 **Расширенные данные для {place_name}**\n"
        f"Координаты: `{lat:.4f}, {lon:.4f}`\n\n"
        f"🌡 Температура: {w['main']['temp']}°C (ощущается {w['main']['feels_like']}°C)\n"
        f"💧 Влажность: {humidity}%\n"
        f"📉 Давление: {w['main']['pressure']} гПа\n"
        f"☁️ Облачность: {w['clouds']['all']}%\n"
        f"👁 Видимость: {vis_part}\n"
        f"💨 Ветер: {wind_part}\n"
        f"🌅 Восход: {sunrise} | Закат: {sunset}\n\n"
        f"🍃 Качество воздуха: {air_info['status']}\n"
        f"{air_info['explanation']}\n\n"
        f"🧪 Загрязнители:\n{pollutants_text}\n\n"
        f"🔆 УФ-индекс: {uv_text}\n"
        f"📝 Описание: {desc}"
    )