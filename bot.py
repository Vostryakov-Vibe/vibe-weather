import telebot
from telebot import types
import os
import sys
import time
import threading
import requests
from dotenv import load_dotenv
import weather_app as weather
import storage
from colorama import Fore, Style, init

init(autoreset=True)
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    print(f"{Fore.RED}[CRITICAL] BOT_TOKEN не найден!")
    sys.exit()

bot = telebot.TeleBot(TOKEN)


def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        "Текущая погода",
        "Прогноз на 5 дней",
        "Моя геолокация",
        "Сравнить города",
        "Расширенные данные",
        "Уведомления",
    )
    return markup


@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.send_message(
        message.chat.id,
        "🌤 Бот активен! Выберите действие:",
        reply_markup=main_menu(),
    )


@bot.message_handler(func=lambda m: m.text == "Текущая погода")
def ask_city(message):
    sent = bot.send_message(
        message.chat.id,
        "Введите город(улицу и дом одной строкой - например: Москва, Тверская 7):",
    )
    bot.register_next_step_handler(sent, process_city_input)


def process_city_input(message):
    query = (message.text or "").strip()
    if not query:
        bot.send_message(
            message.chat.id,
            "❌ Пустой запрос. Попробуйте ещё раз.",
            reply_markup=main_menu(),
        )
        return

    # Сначала пробуем распознать полный адрес (город, улица, дом)
    geo = weather.geocode_address(query)
    if geo:
        lat, lon, place = geo
    else:
        # Фоллбэк на поиск только по названию города через OpenWeather
        coords = weather.get_coordinates(query)
        if not coords:
            bot.send_message(
                message.chat.id,
                "❌ Адрес или город не найден.",
                reply_markup=main_menu(),
            )
            return
        lat, lon = coords
        place = query

    msg = weather.get_full_card(lat, lon, place)
    bot.send_message(
        message.chat.id,
        msg,
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


@bot.message_handler(func=lambda m: m.text == "Моя геолокация")
def request_geo(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("Отправить локацию 📍", request_location=True))
    bot.send_message(
        message.chat.id,
        "Нажмите кнопку для отправки координат:",
        reply_markup=markup,
    )


@bot.message_handler(content_types=['location'])
def handle_location(message):
    lat, lon = message.location.latitude, message.location.longitude
    user = storage.load_user(message.from_user.id)

    # Определяем человекочитаемый адрес по координатам,
    # чтобы пользователь видел осмысленное название локации
    full_address = weather.reverse_geocode(lat, lon)
    user.update({"lat": lat, "lon": lon, "city": full_address})
    storage.save_user(message.from_user.id, user)

    # Отправляем карту и карточку погоды по сохранённым координатам
    bot.send_location(message.chat.id, lat, lon)
    msg = weather.get_full_card(lat, lon, full_address)
    bot.send_message(
        message.chat.id,
        "✅ Координаты сохранены!",
        reply_markup=main_menu(),
    )
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "Сравнить города")
def compare_start(message):
    sent = bot.send_message(
        message.chat.id,
        "Введите 2 города через запятую (напр: Москва, Сочи):",
    )
    bot.register_next_step_handler(sent, process_compare)


def process_compare(message):
    cities = [c.strip() for c in (message.text or "").split(',')]
    if len(cities) < 2:
        bot.send_message(
            message.chat.id,
            "Нужно 2 города.",
            reply_markup=main_menu(),
        )
        return

    res = "📊 **Сравнение температур:**\n"
    for city in cities[:2]:
        coords = weather.get_coordinates(city)
        if coords:
            lat, lon = coords
            w = weather.get_current_weather(lat, lon)
            if w and 'main' in w:
                res += f"📍 {city.capitalize()}: {w['main']['temp']}°C\n"
        else:
            res += f"❌ {city}: Не найден\n"
    bot.send_message(
        message.chat.id,
        res,
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


@bot.message_handler(func=lambda m: m.text == "Прогноз на 5 дней")
def forecast_btn(message):
    user = storage.load_user(message.from_user.id)
    if not user.get('lat'):
        bot.send_message(
            message.chat.id,
            "Сначала отправьте геолокацию.",
            reply_markup=main_menu(),
        )
        return
    place_name = user.get('city') or "Ваша локация"
    fc = weather.get_forecast_5d3h(user['lat'], user['lon'])

    # Краткий агрегированный прогноз по дням (5 дней вперёд)
    text = weather.format_daily_forecast_summary(place_name, fc)

    # Кнопки для раскрытия детального прогноза по каждому дню
    markup = types.InlineKeyboardMarkup()
    # Берём список дат из готового агрегатора, чтобы не дублировать логику группировки
    from weather_app import summarize_forecast_by_days

    daily = summarize_forecast_by_days(fc)
    for d in daily:
        date_str = d["date"]
        # Красивое отображение даты на кнопке
        try:
            from datetime import datetime as _dt

            dt_obj = _dt.strptime(date_str, "%Y-%m-%d")
            btn_text = dt_obj.strftime("%d.%m (%a)")
        except Exception:
            btn_text = date_str
        markup.add(
            types.InlineKeyboardButton(
                btn_text,
                callback_data=f"fcday_{date_str}",
            )
        )

    bot.send_message(
        message.chat.id,
        text,
        parse_mode="Markdown",
        reply_markup=markup if daily else main_menu(),
    )


@bot.message_handler(func=lambda m: m.text == "Расширенные данные")
def air_btn(message):
    user = storage.load_user(message.from_user.id)
    if not user.get('lat'):
        bot.send_message(
            message.chat.id,
            "Укажите локацию (через отправку геолокации).",
            reply_markup=main_menu(),
        )
        return

    place_name = user.get('city') or "Ваша локация"
    msg = weather.get_extended_card(user['lat'], user['lon'], place_name)
    bot.send_message(
        message.chat.id,
        msg,
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


@bot.message_handler(func=lambda m: m.text == "Уведомления")
def notify_btn(message):
    user = storage.load_user(message.from_user.id)
    # Гарантируем наличие секции уведомлений
    user.setdefault('notifications', {"enabled": False, "interval_h": 2, "last_ts": 0})
    storage.save_user(message.from_user.id, user)

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("30 минут", callback_data="notify_0.5"),
        types.InlineKeyboardButton("1 час", callback_data="notify_1"),
        types.InlineKeyboardButton("2 часа", callback_data="notify_2"),
    )
    markup.add(
        types.InlineKeyboardButton("❌ Выключить", callback_data="notify_off")
    )

    current = "выключены"
    if user['notifications'].get('enabled'):
        interval = user['notifications'].get('interval_h', 2)
        if interval == 0.5:
            current = "каждые 30 минут"
        elif interval == 1:
            current = "каждый час"
        else:
            current = f"каждые {int(interval)} часа"

    bot.send_message(
        message.chat.id,
        f"Текущие уведомления: {current}\nВыберите новый интервал:",
        reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("notify_"))
def notify_callback(call):
    user = storage.load_user(call.from_user.id)
    user.setdefault('notifications', {"enabled": False, "interval_h": 2, "last_ts": 0})

    data = call.data
    if data == "notify_off":
        user['notifications']['enabled'] = False
        text_status = "Уведомления: ВЫКЛ ❌"
    else:
        interval = float(data.split("_")[1])
        user['notifications']['enabled'] = True
        user['notifications']['interval_h'] = interval
        if interval == 0.5:
            human = "каждые 30 минут"
        elif interval == 1:
            human = "каждый час"
        else:
            human = f"каждые {int(interval)} часа"
        text_status = f"Уведомления: ВКЛ ✅ ({human})"

    storage.save_user(call.from_user.id, user)
    bot.answer_callback_query(call.id, "Сохранено")
    bot.send_message(
        call.message.chat.id,
        text_status,
        reply_markup=main_menu(),
    )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("fcday_"))
def forecast_day_callback(call):
    """
    Обработчик нажатий на кнопки с выбором дня для детального прогноза.
    """
    date_str = call.data.split("fcday_", 1)[1]
    user = storage.load_user(call.from_user.id)
    if not user.get("lat"):
        bot.answer_callback_query(call.id, "Сначала отправьте геолокацию.")
        return

    fc = weather.get_forecast_5d3h(user["lat"], user["lon"])
    place_name = user.get("city") or "Ваша локация"
    text = weather.format_single_day_details(place_name, fc, date_str)

    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        text,
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


def notifications_worker():
    """
    Фоновый поток, который периодически проверяет всех пользователей
    и отправляет им карточку погоды согласно выбранному интервалу уведомлений.
    """
    while True:
        try:
            all_users = storage.load_all_users()
        except Exception as e:
            print(f"{Fore.RED}[ERROR] Не удалось загрузить пользователей для уведомлений: {e}")
            time.sleep(60)
            continue

        now_ts = time.time()
        for user_id_str, user in all_users.items():
            try:
                notif = user.get("notifications") or {}
                if not notif.get("enabled"):
                    continue

                lat = user.get("lat")
                lon = user.get("lon")
                if lat is None or lon is None:
                    continue

                interval_h = float(notif.get("interval_h", 2))
                last_ts = float(notif.get("last_ts", 0))
                if interval_h <= 0:
                    continue

                if now_ts - last_ts < interval_h * 3600:
                    continue

                chat_id = int(user_id_str)
                place_name = user.get("city") or "Ваша локация"
                msg = weather.get_full_card(lat, lon, place_name)
                bot.send_message(chat_id, f"⏰ Погодное уведомление:\n\n{msg}", parse_mode="Markdown")

                # Обновляем время последней отправки
                notif["last_ts"] = now_ts
                user["notifications"] = notif
                storage.save_user(chat_id, user)
            except Exception as e:
                print(f"{Fore.YELLOW}[WARN] Ошибка при отправке уведомления пользователю {user_id_str}: {e}")

        # Проверяем раз в минуту
        time.sleep(60)


if __name__ == "__main__":
    print(f"{Fore.GREEN}[SUCCESS] Бот запущен и работает...")

    # Запускаем фоновый поток с уведомлениями
    threading.Thread(target=notifications_worker, daemon=True).start()

    # Перезапускаем polling при сетевых таймаутах, чтобы избежать падения бота
    while True:
        try:
            bot.polling(none_stop=True, timeout=25, long_polling_timeout=25)
        except requests.exceptions.ReadTimeout:
            print(f"{Fore.YELLOW}[WARN] ReadTimeout от Telegram API, перезапуск polling...")
            time.sleep(3)
            continue
        except Exception as e:
            print(f"{Fore.RED}[ERROR] Ошибка в polling: {e}")
            time.sleep(5)