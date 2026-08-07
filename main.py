import logging
from telegram import (Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
                      InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat)
from telegram.ext import (Application, ApplicationHandlerStop, CommandHandler, ConversationHandler,
                          MessageHandler, CallbackQueryHandler, filters, ContextTypes)
from telegram.error import BadRequest, TimedOut, NetworkError, RetryAfter
from typing import Dict
import json
import os
import glob
import re
import html
import shutil
import time
import asyncio
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

os.environ['TZ'] = 'Europe/Moscow'
time.tzset()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    logger.critical("❌ Токен бота не найден! Установи BOT_TOKEN в переменных окружения.")
    raise ValueError("BOT_TOKEN is not set")
GROUP_CHAT_ID = -1003959278251

NOTIFY_HTTP_HOST = "0.0.0.0"
NOTIFY_HTTP_PORT = int(os.getenv("PORT") or os.getenv("HTTP_PORT") or 8799)
NOTIFY_SECRET = "yangBot_rafyl_2026"
SEARCH_SESSION_HASH = "-1874601906"
DATA_FILE = "user_data.json"
USER_LINKS_HISTORY_FILE = "user_links_history.json"
REPORTED_MATCHES_FILE = "reported_matches.json"
MATCH_VOTES_FILE = "match_votes.json"
LINK_VERDICTS_FILE = "link_verdicts.json"
ACHIEVEMENTS_FILE = "achievements.json"
SESSION_STATE_FILE = "session_state.json"
REMIND_COOLDOWN_SECONDS = 300
MAX_LINKS = 5
MAX_NAME_LENGTH = 30
LINK_YANG_SEP = "|"
ADMIN_IDS = [969984835]
BROADCAST_IDS = [1467875376, 546938924, 1252967508, 596548043, 576391595]
ACCESS_FILE = "access.json"
ACCESS_CONTROL = True
AUTO_APPROVE_GROUP_MEMBERS = False
ACCESS_REQUEST_COOLDOWN = 3600
VOTE_PAIRS = [
    (596548043, 576391595),
]

PERSONAL_NOTIFY_IDS = (596548043, 576391595)
PRIVATE_MATCH_DELIVERY = True
GROUP_MATCH_ARCHIVE = False
CHECK_COOLDOWN = 10
LINKS_TTL_DAYS = 2
CLEANUP_INTERVAL_HOURS = 6
AUTO_CHECK_ON_SAVE = True
AUTO_CHECK_DEFAULT = True
SUBMISSION_STACK_LIMIT = 5
BACKUP_KEEP = 5
TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 4000

NET_CONNECT_TIMEOUT = 20.0
NET_READ_TIMEOUT = 45.0
NET_WRITE_TIMEOUT = 60.0
NET_POOL_TIMEOUT = 20.0
NET_POOL_SIZE = 16
NET_GETUPDATES_READ_TIMEOUT = 70.0
NET_LONGPOLL_TIMEOUT = 30
NET_TRIES = 5
NET_BACKOFF_START = 1.0
NET_BACKOFF_FACTOR = 1.8
NET_BACKOFF_MAX = 20.0


async def tg(fn, *args, _tries=NET_TRIES, **kwargs):
    delay = NET_BACKOFF_START
    last = None
    name = getattr(fn, "__name__", str(fn))
    for attempt in range(1, _tries + 1):
        try:
            return await fn(*args, **kwargs)
        except RetryAfter as e:
            last = e
            wait = float(getattr(e, "retry_after", 1) or 1) + 0.5
            logger.warning(f"NET {name}: flood-limit, ждём {wait:.1f}с "
                           f"(попытка {attempt}/{_tries})")
            if attempt == _tries:
                break
            await asyncio.sleep(wait)
        except BadRequest:
            raise
        except (TimedOut, NetworkError) as e:
            last = e
            if attempt == _tries:
                break
            logger.warning(f"NET {name}: {type(e).__name__}: {e} — "
                           f"повтор через {delay:.1f}с (попытка {attempt}/{_tries})")
            await asyncio.sleep(delay)
            delay = min(delay * NET_BACKOFF_FACTOR, NET_BACKOFF_MAX)
    logger.error(f"NET {name}: не удалось за {_tries} попыток: {last}")
    raise last


async def tg_answer(q, *args, **kwargs):
    try:
        return await tg(q.answer, *args, _tries=2, **kwargs)
    except Exception as e:
        logger.warning(f"NET answer_callback_query не прошёл: {e}")
        return None

WAITING_FOR_NAME = 1
WAITING_FOR_LINKS = 2
WAITING_FOR_NEW_NAME = 3


INVALID_NAME_CHARS = ['@', '#', '$', '%', '^', '&', '*', '(', ')', '[', ']',
                      '{', '}', '\\', '|', '/', '`', '~', '<', '>', '"', "'"]


def validate_name(text: str):
    if not isinstance(text, str):
        return "❌ Имя не может быть пустым."
    if text in ALL_BUTTONS:
        return "❌ Это нельзя использовать как имя. Введи своё настоящее имя:"
    if not text.strip():
        return "❌ Имя не может быть пустым."
    for char in text:
        if char in "\r\n\t" or ord(char) < 32 or 0x200b <= ord(char) <= 0x200f \
                or 0x202a <= ord(char) <= 0x202e or ord(char) == 0xfeff:
            return "❌ Имя содержит невидимый или служебный символ. Введи обычное имя:"
    if len(text) < 1:
        return "❌ Имя не может быть пустым."
    if len(text) > MAX_NAME_LENGTH:
        return f"❌ Слишком длинное имя (макс {MAX_NAME_LENGTH} символов)."
    for char in INVALID_NAME_CHARS:
        if char in text:
            return f"❌ Имя содержит недопустимый символ '{char}'."
    return None


def _atomic_write_json(path: str, data) -> None:
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _save_json(path: str, data) -> bool:
    try:
        _atomic_write_json(path, data)
    except Exception as e:
        logger.error(f"Ошибка сохранения {path}: {e}")
        return False
    try:
        shutil.copy2(path, f"{path}.backup")
    except Exception as e:
        logger.warning(f"Не удалось обновить бэкап {path}: {e}")
    return True


def _load_json(path: str, default):
    for src in (path, f"{path}.backup"):
        if not os.path.exists(src):
            continue
        try:
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Не удалось прочитать {src}: {e}")
            continue
        if src != path:
            logger.warning(f"⚠️ {path} повреждён — восстановлен из .backup")
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            try:
                if os.path.exists(path):
                    os.replace(path, f"{path}.corrupt_{stamp}")
                    logger.info(f"Повреждённый файл сохранён как {path}.corrupt_{stamp}")
            except Exception as e:
                logger.warning(f"Не удалось отложить повреждённый {path}: {e}")
            _save_json(path, data)
        return data
    return default


def load_data():
    data = _load_json(DATA_FILE, None)
    if not isinstance(data, dict):
        return {}
    result = {}
    for k, v in data.items():
        try:
            uid = int(k)
        except (TypeError, ValueError):
            logger.warning(f"Пропущен нечисловой ключ в {DATA_FILE}: {k!r}")
            continue
        if isinstance(v, dict) and isinstance(v.get("links"), dict):
            links = {}
            for r, ld in v["links"].items():
                try:
                    links[int(r)] = ld
                except (TypeError, ValueError):
                    continue
            v["links"] = links
        result[uid] = v
    return result


# Кэш строк топа. Экран собирается дважды подряд (текст + клавиатура), а каждая
# сборка пересчитывает метрики всем участникам по 120 дням истории — на полном
# составе это сотни миллисекунд на одно нажатие кнопки. Кэш живёт до ближайшей
# записи в achievements, поэтому устаревших чисел показать не может.
_ach_data_version = 0
_ach_top_rows_cache = {}
ACH_TOP_CACHE_MAX = 32


def save_data():
    # имя и опт-аут участника попадают в строки топа, поэтому их правка тоже
    # обязана сбрасывать кэш _ach_top_rows — иначе экран покажет старое имя
    global _ach_data_version
    _ach_data_version += 1
    _ach_top_rows_cache.clear()
    return _save_json(DATA_FILE, user_data)


def load_user_links_history():
    data = _load_json(USER_LINKS_HISTORY_FILE, None)
    if not isinstance(data, dict):
        return {}
    result = {}
    for k, v in data.items():
        try:
            uid = int(k)
        except (TypeError, ValueError):
            continue
        result[uid] = v if isinstance(v, list) else []
    return result


def save_user_links_history(h):
    return _save_json(USER_LINKS_HISTORY_FILE, h)


def load_reported_matches():
    data = _load_json(REPORTED_MATCHES_FILE, None)
    return data if isinstance(data, dict) else {}


def save_reported_matches(m):
    return _save_json(REPORTED_MATCHES_FILE, m)


def load_match_votes():
    data = _load_json(MATCH_VOTES_FILE, None)
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def save_match_votes():
    return _save_json(MATCH_VOTES_FILE, match_votes)


def load_link_verdicts():
    data = _load_json(LINK_VERDICTS_FILE, None)
    return data if isinstance(data, dict) else {}


def save_link_verdicts():
    return _save_json(LINK_VERDICTS_FILE, link_verdicts)


user_data: Dict[int, dict] = load_data()


def _drop_legacy_skill_keys() -> int:
    removed = 0
    for _d in user_data.values():
        if not isinstance(_d, dict):
            continue
        for _k in ("session_skill_queue", "skill_last_epoch"):
            if _k in _d:
                _d.pop(_k, None)
                removed += 1
    return removed


_legacy_removed = _drop_legacy_skill_keys()
user_links_history: Dict[int, list] = load_user_links_history()
reported_matches: Dict[str, list] = load_reported_matches()
match_votes: Dict[str, dict] = load_match_votes()
link_verdicts: Dict[str, dict] = load_link_verdicts()


# ===================== ДОСТИЖЕНИЯ ====================

ACH_TIER_ICONS = ["🥉", "🥈", "🥇", "💎"]
ACH_TIER_NAMES = ["Бронза", "Серебро", "Золото", "Платина"]
ACH_XP_BY_TIER = [10, 25, 60, 150]
ACH_HIDDEN_XP = 100
ACH_DAYS_KEEP = 120
ACH_FAST_VOTE_SECONDS = 120
ACH_LIGHTNING_SECONDS = 30
ACH_SKILL_HI = 90.0
ACH_SKILL_LOW = 80.0
ACH_SKILL_MIN_SAMPLES = 2
ACH_TOP_LIMIT = 15
# uid админов, у которых сейчас включён режим "показывать скрытых" на экране топа.
# Это чисто состояние экрана (не персистентное), сбрасывается при рестарте бота.
_admin_top_hidden_view = set()
# uid, у которых на экране топа сейчас развёрнут полный список рекордов.
# Как и _admin_top_hidden_view — состояние экрана, не персистится.
_top_records_full = set()
ACH_UNITS_VERSION = 2      # 1 = сессия считалась по URL, 2 = по отправленной пачке
ACH_WEEK_GOAL = 30         # порог «стабильной недели» в пачках
ACH_BTN = "🏆 Достижения"

ACH_CATEGORIES = [
    ("vol", "📦 Объём"),
    ("streak", "🔥 Серии"),
    ("vote", "⚖️ Оценки"),
    ("skill", "📈 Навык"),
    ("match", "🎯 Совпадения"),
    ("style", "🌙 Стиль"),
    ("time", "🗓️ Дистанция"),
    ("secret", "🕵️ Скрытые"),
]

ACHIEVEMENTS_DEF = [
    {"code": "sess_total", "cat": "vol", "name": "Конвейер",
     "desc": "Отправлено сессий всего", "metric": "sessions_total",
     "tiers": [15, 75, 300, 1500], "unit": "сессий"},
    {"code": "sess_day", "cat": "vol", "name": "Спринтер",
     "desc": "Сессий за один день", "metric": "sessions_day",
     "tiers": [6, 15, 30, 60], "unit": "за день"},
    {"code": "sess_week", "cat": "vol", "name": "Марафонец",
     "desc": "Сессий за 7 дней подряд", "metric": "sessions_week",
     "tiers": [45, 120, 270, 600], "unit": "за неделю"},
    {"code": "full_pack", "cat": "vol", "name": "Полная пачка",
     "desc": "Сессий, отправленных сразу по 5 ссылок", "metric": "full_packs",
     "tiers": [25, 100, 500], "unit": "сессий"},
    {"code": "sess_month", "cat": "vol", "name": "Месячная норма",
     "desc": "Сессий за 30 дней подряд", "metric": "sessions_month",
     "tiers": [150, 450, 1200, 3000], "unit": "за 30 дней"},
    {"code": "urls_total", "cat": "vol", "name": "Разметчик",
     "desc": "Размечено ссылок всего (по всем сессиям)", "metric": "urls_total",
     "tiers": [100, 500, 2000, 8000], "unit": "ссылок"},
    {"code": "pack_avg", "cat": "vol", "name": "Под завязку",
     "desc": "Сессий подряд по 5 ссылок без неполных", "metric": "full_streak",
     "tiers": [10, 30, 100], "unit": "подряд"},
    {"code": "sess_hour", "cat": "vol", "name": "Турбо-час",
     "desc": "Сессий за один час", "metric": "sessions_hour",
     "tiers": [3, 6, 10, 15], "unit": "за час"},

    {"code": "day_streak", "cat": "streak", "name": "Не пропускаю",
     "desc": "Дней подряд минимум с одной сессией", "metric": "streak_days",
     "tiers": [3, 7, 21, 60], "unit": "дней"},
    {"code": "week_100", "cat": "streak", "name": "Стабильная неделя",
     "desc": f"Недель подряд с {ACH_WEEK_GOAL}+ сессиями", "metric": "weeks_100",
     "tiers": [2, 4, 12], "unit": "недель"},
    {"code": "weeks_active", "cat": "streak", "name": "В обойме",
     "desc": "Недель подряд хотя бы с одной сессией", "metric": "weeks_active",
     "tiers": [4, 12, 26, 52], "unit": "недель"},
    {"code": "month_full", "cat": "streak", "name": "Полный месяц",
     "desc": "Активных дней в одном календарном месяце", "metric": "month_days",
     "tiers": [15, 22, 28], "unit": "дней"},
    {"code": "vote_streak", "cat": "streak", "name": "Дежурный судья",
     "desc": "Дней подряд минимум с одной оценкой", "metric": "vote_streak_days",
     "tiers": [3, 7, 21], "unit": "дней"},

    {"code": "vote_total", "cat": "vote", "name": "Арбитр",
     "desc": "Выставлено оценок всего", "metric": "votes_total",
     "tiers": [50, 250, 1000, 3000], "unit": "оценок"},
    {"code": "vote_2d", "cat": "vote", "name": "Судейский заплыв",
     "desc": "Оценок за 2 дня", "metric": "votes_2d",
     "tiers": [30, 60, 100, 150], "unit": "за 2 дня"},
    {"code": "vote_final", "cat": "vote", "name": "Последнее слово",
     "desc": "Финальных вердиктов", "metric": "final_votes",
     "tiers": [25, 100, 500], "unit": "финалок"},
    {"code": "vote_fast", "cat": "vote", "name": "Реакция",
     "desc": f"Оценок быстрее чем за {ACH_FAST_VOTE_SECONDS // 60} мин после уведомления",
     "metric": "fast_votes", "tiers": [10, 50, 200], "unit": "раз"},
    {"code": "vote_day", "cat": "vote", "name": "Разбор полётов",
     "desc": "Оценок за один день", "metric": "votes_day",
     "tiers": [15, 30, 60, 120], "unit": "за день"},
    {"code": "vote_light", "cat": "vote", "name": "Молния",
     "desc": f"Оценок быстрее {ACH_LIGHTNING_SECONDS} сек после уведомления",
     "metric": "lightning_votes", "tiers": [5, 25, 100], "unit": "раз"},

    {"code": "skill_peak", "cat": "skill", "name": "Планка",
     "desc": "Максимальный навык за всё время", "metric": "skill_peak",
     "tiers": [90, 95, 98], "unit": "навык"},
    {"code": "skill_hi", "cat": "skill", "name": "Держу 90+",
     "desc": "Дней, где навык не опускался ниже 90", "metric": "skill_hi_days",
     "tiers": [1, 3, 7, 30], "unit": "дней"},
    {"code": "skill_nofall", "cat": "skill", "name": "Только вверх",
     "desc": "Дней подряд без падения навыка", "metric": "skill_nofall",
     "tiers": [3, 7, 21], "unit": "дней"},
    {"code": "skill_comeback", "cat": "skill", "name": "Камбэк",
     "desc": "Подъёмов навыка с 80− обратно на 90+", "metric": "comebacks",
     "tiers": [1, 5, 15], "unit": "раз"},
    {"code": "skill_ups", "cat": "skill", "name": "По ступенькам",
     "desc": "Сколько раз навык вырос", "metric": "skill_ups",
     "tiers": [10, 50, 200], "unit": "подъёмов"},
    {"code": "skill_zone", "cat": "skill", "name": "Зона 90+",
     "desc": "Дней подряд с навыком 90 и выше", "metric": "skill90_streak",
     "tiers": [3, 7, 21, 60], "unit": "дней"},

    {"code": "match_total", "cat": "match", "name": "Пересечение",
     "desc": "Найдено совпадений", "metric": "matches_total",
     "tiers": [10, 50, 200, 500], "unit": "совпадений"},
    {"code": "match_people", "cat": "match", "name": "Свой круг",
     "desc": "Разных участников в твоих совпадениях", "metric": "partners_unique",
     "tiers": [5, 15, 30], "unit": "человек"},
    {"code": "match_day", "cat": "match", "name": "Час пик",
     "desc": "Совпадений за один день", "metric": "matches_day",
     "tiers": [3, 10, 25], "unit": "за день"},
    {"code": "match_soul", "cat": "match", "name": "Родственная душа",
     "desc": "Совпадений с одним и тем же участником", "metric": "partner_best",
     "tiers": [10, 50, 150], "unit": "с одним"},
    {"code": "match_burst", "cat": "match", "name": "Залп",
     "desc": "Совпадений за одну проверку", "metric": "matches_burst",
     "tiers": [3, 7, 15], "unit": "за проверку"},

    {"code": "night", "cat": "style", "name": "Ночная смена",
     "desc": "Дней с сессиями между 00:00 и 05:00", "metric": "night_days",
     "tiers": [5, 25, 100], "unit": "дней"},
    {"code": "early", "cat": "style", "name": "Ранняя пташка",
     "desc": "Дней с сессиями до 07:00", "metric": "early_days",
     "tiers": [5, 25, 100], "unit": "дней"},
    {"code": "clean", "cat": "style", "name": "Без брака",
     "desc": "Отправок подряд без отмены", "metric": "clean_streak",
     "tiers": [20, 100, 500], "unit": "отправок"},
    {"code": "weekend", "cat": "style", "name": "Выходной не помеха",
     "desc": "Дней с сессиями в субботу или воскресенье", "metric": "weekend_days",
     "tiers": [5, 25, 100], "unit": "дней"},
    {"code": "long_day", "cat": "style", "name": "Длинный день",
     "desc": "Разных часов с отправками за один день", "metric": "day_hours",
     "tiers": [4, 6, 9], "unit": "часов"},

    {"code": "veteran", "cat": "time", "name": "Ветеран",
     "desc": "Активных дней всего", "metric": "active_days",
     "tiers": [30, 100, 250, 500], "unit": "дней"},
    {"code": "tenure", "cat": "time", "name": "Стаж",
     "desc": "Дней с первой отправки", "metric": "tenure_days",
     "tiers": [30, 180, 365], "unit": "дней"},
    {"code": "hours_map", "cat": "time", "name": "Карта суток",
     "desc": "Разных часов суток, в которые ты отправлял сессии",
     "metric": "hours_seen", "tiers": [8, 16, 24], "unit": "часов"},

    {"code": "first_match", "cat": "secret", "name": "Первый контакт",
     "desc": "Первое в жизни совпадение", "metric": "matches_total",
     "tiers": [1], "hidden": True, "unit": ""},
    {"code": "century", "cat": "secret", "name": "Сотка",
     "desc": "100 дней серии подряд", "metric": "streak_days",
     "tiers": [100], "hidden": True, "unit": ""},
    {"code": "perfect", "cat": "secret", "name": "Идеал",
     "desc": "Навык 100", "metric": "skill_peak",
     "tiers": [100], "hidden": True, "unit": ""},
    {"code": "month_clean", "cat": "secret", "name": "Хирург",
     "desc": "30 дней без единой отмены", "metric": "no_undo_days",
     "tiers": [30], "hidden": True, "unit": ""},
    {"code": "first_step", "cat": "secret", "name": "Начало положено",
     "desc": "Первая отправленная сессия", "metric": "sessions_total",
     "tiers": [1], "hidden": True, "unit": ""},
    {"code": "lucky_777", "cat": "secret", "name": "Счастливое число",
     "desc": "777 размеченных ссылок", "metric": "urls_total",
     "tiers": [777], "hidden": True, "unit": ""},
    {"code": "owl", "cat": "secret", "name": "Сова",
     "desc": "7 ночных дней подряд", "metric": "night_streak",
     "tiers": [7], "hidden": True, "unit": ""},
    {"code": "phoenix", "cat": "secret", "name": "Феникс",
     "desc": "10 камбэков навыка", "metric": "comebacks",
     "tiers": [10], "hidden": True, "unit": ""},
    {"code": "deep_night", "cat": "secret", "name": "Глубокая ночь",
     "desc": "10 дней с отправками между 03:00 и 05:00",
     "metric": "deep_night_days", "tiers": [10], "hidden": True, "unit": ""},
    {"code": "first_vote", "cat": "secret", "name": "Голос отдан",
     "desc": "Первая выставленная оценка", "metric": "votes_total",
     "tiers": [1], "hidden": True, "unit": ""},
    {"code": "iron", "cat": "secret", "name": "Железный человек",
     "desc": "Серия 30 дней подряд", "metric": "streak_days",
     "tiers": [30], "hidden": True, "unit": ""},
]

ACH_BY_CODE = {a["code"]: a for a in ACHIEVEMENTS_DEF}


def _ach_need_key(need):
    """Ключ тира — по значению порога, не по индексу: правка тиров не ломает историю."""
    try:
        f = float(need)
    except (TypeError, ValueError):
        return str(need)
    return str(int(f)) if f.is_integer() else str(f)


def _ach_unlock_key(code, need):
    return f"{code}:{_ach_need_key(need)}"


def _ach_tier_xp(a, ti):
    if a.get("hidden"):
        return ACH_HIDDEN_XP
    return ACH_XP_BY_TIER[min(ti, len(ACH_XP_BY_TIER) - 1)]


def _ach_unlocked_tiers(rec):
    """Взятые тиры, которые есть в текущем наборе достижений."""
    got = rec.get("unlocked") or {}
    out = []
    for a in ACHIEVEMENTS_DEF:
        for ti, need in enumerate(a["tiers"]):
            if _ach_unlock_key(a["code"], need) in got:
                out.append((a, ti, need))
    return out


def _ach_tiers_taken(rec):
    return len(_ach_unlocked_tiers(rec))


def _ach_xp(rec):
    """XP считается от текущих порогов, а не копится. Меняешь тиры — XP едет за ними."""
    total = sum(_ach_tier_xp(a, ti) for a, ti, _need in _ach_unlocked_tiers(rec))
    rec["xp"] = total
    return total


def _ach_migrate(rec):
    """v1 -> v2: сессия = пачка (а не URL) + ключи тиров по значению порога."""
    try:
        ver = int(rec.get("v", 1) or 1)
    except (TypeError, ValueError):
        ver = 1
    if ver >= ACH_UNITS_VERSION:
        return False

    def _i(src, key):
        try:
            return int((src or {}).get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    c = rec["counters"]
    urls = _i(c, "urls") or _i(c, "sessions")
    packs = _i(c, "packs")
    c["urls"] = urls
    c["sessions"] = packs if packs > 0 else urls
    # старая посуточная статистика в URL — сжимаем средним размером пачки
    ratio = 1.0
    if urls > 0 and 0 < packs <= urls:
        ratio = packs / float(urls)
    if ratio < 1.0:
        for d in (rec.get("days") or {}).values():
            if not isinstance(d, dict):
                continue
            du = _i(d, "sessions")
            if du > 0:
                d["urls"] = du
                d["sessions"] = max(1, int(round(du * ratio)))
            for h in range(24):
                hk = f"h{h}"
                hv = _i(d, hk)
                if hv > 0:
                    d[hk] = max(1, int(round(hv * ratio)))
        bests = rec.get("bests")
        if isinstance(bests, dict):
            for k in ("sessions_day", "sessions_week", "sessions_month", "sessions_hour"):
                bv = _i(bests, k)
                if bv > 0:
                    bests[k] = max(1, int(round(bv * ratio)))
    else:
        for d in (rec.get("days") or {}).values():
            if isinstance(d, dict) and _i(d, "sessions") > 0 and "urls" not in d:
                d["urls"] = _i(d, "sessions")

    old = rec.get("unlocked") or {}
    new = {}
    for key, when in old.items():
        code, _sep, idx = str(key).partition(":")
        a = ACH_BY_CODE.get(code)
        if not a:
            continue
        try:
            ti = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= ti < len(a["tiers"]):
            new[_ach_unlock_key(code, a["tiers"][ti])] = when
    rec["unlocked"] = new
    rec["v"] = ACH_UNITS_VERSION
    _ach_xp(rec)
    return True


def load_achievements():
    data = _load_json(ACHIEVEMENTS_FILE, None)
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        try:
            uid = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict):
            out[uid] = v
    return out


def save_achievements():
    # любая запись инвалидирует кэш строк топа (см. _ach_top_rows)
    global _ach_data_version
    _ach_data_version += 1
    _ach_top_rows_cache.clear()
    return _save_json(ACHIEVEMENTS_FILE, achievements)


def _ach_rec(uid):
    rec = achievements.get(uid)
    is_new = not isinstance(rec, dict)
    if is_new:
        rec = {}
        achievements[uid] = rec
    if not isinstance(rec.get("counters"), dict):
        rec["counters"] = {}
    if not isinstance(rec.get("days"), dict):
        rec["days"] = {}
    if not isinstance(rec.get("streak"), dict):
        rec["streak"] = {"cur": 0, "best": 0, "last_day": ""}
    if not isinstance(rec.get("skill"), dict):
        rec["skill"] = {"peak": 0, "hi_days": 0, "below": False}
    if not isinstance(rec.get("bests"), dict):
        rec["bests"] = {}
    if not isinstance(rec.get("partners"), list):
        rec["partners"] = []
    if not isinstance(rec.get("hours"), list):
        rec["hours"] = []
    if not isinstance(rec.get("partner_counts"), dict):
        rec["partner_counts"] = {}
    if not isinstance(rec.get("unlocked"), dict):
        rec["unlocked"] = {}
    try:
        rec["xp"] = int(rec.get("xp", 0) or 0)
    except (TypeError, ValueError):
        rec["xp"] = 0
    _ach_migrate(rec)
    if is_new:
        save_achievements()
    return rec


def _ach_local_dt(uid, dt=None):
    dt = dt or datetime.now()
    off = _user_utc_offset(uid)
    return dt + timedelta(hours=off) if off else dt


def _ach_day_key(uid, dt=None):
    return _ach_local_dt(uid, dt).strftime("%Y-%m-%d")


def _ach_day(rec, day):
    d = rec["days"].get(day)
    if not isinstance(d, dict):
        d = {}
        rec["days"][day] = d
    return d


def _ach_prune(rec):
    days = rec.get("days") or {}
    if len(days) <= ACH_DAYS_KEEP:
        return
    for k in sorted(days.keys())[:-ACH_DAYS_KEEP]:
        days.pop(k, None)


def _ach_bump(rec, key, n=1):
    c = rec["counters"]
    try:
        cur = int(c.get(key, 0) or 0)
    except (TypeError, ValueError):
        cur = 0
    c[key] = max(0, cur + n)
    return c[key]


def _ach_day_bump(rec, day, key, n=1):
    d = _ach_day(rec, day)
    try:
        cur = int(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        cur = 0
    d[key] = max(0, cur + n)
    return d[key]


def _ach_parse_day(k):
    try:
        return datetime.strptime(k, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _ach_window_max(rec, field, window):
    days = rec.get("days") or {}
    vals = {}
    for k, d in days.items():
        dt = _ach_parse_day(k)
        if dt is None or not isinstance(d, dict):
            continue
        try:
            vals[dt] = int(d.get(field, 0) or 0)
        except (TypeError, ValueError):
            continue
    if not vals:
        return 0
    best = 0
    for anchor in vals:
        s = 0
        for i in range(window):
            s += vals.get(anchor - timedelta(days=i), 0)
        if s > best:
            best = s
    return best


def _ach_weeks_streak(rec, need=100):
    weeks = {}
    for k, d in (rec.get("days") or {}).items():
        dt = _ach_parse_day(k)
        if dt is None or not isinstance(d, dict):
            continue
        monday = dt - timedelta(days=dt.weekday())
        try:
            weeks[monday] = weeks.get(monday, 0) + int(d.get("sessions", 0) or 0)
        except (TypeError, ValueError):
            continue
    if not weeks:
        return 0
    best = cur = 0
    prev = None
    for wk in sorted(weeks):
        if weeks[wk] < need:
            cur = 0
            prev = wk
            continue
        if prev is not None and cur and (wk - prev).days == 7:
            cur += 1
        else:
            cur = 1
        prev = wk
        if cur > best:
            best = cur
    return best


def _ach_skill_nofall(rec):
    seq = []
    for k in sorted((rec.get("days") or {}).keys()):
        d = rec["days"].get(k) or {}
        v = d.get("skill_last")
        if v is None:
            continue
        try:
            seq.append(float(v))
        except (TypeError, ValueError):
            continue
    best = cur = 0
    for i, v in enumerate(seq):
        if i and v >= seq[i - 1]:
            cur += 1
        else:
            cur = 1
        if cur > best:
            best = cur
    return best


def _ach_no_undo_days(rec):
    days = rec.get("days") or {}
    keys = sorted(k for k in days if _ach_parse_day(k))
    if not keys:
        return 0
    last = _ach_parse_day(keys[-1])
    base = _ach_parse_day(rec.get("last_undo_day") or "") or _ach_parse_day(keys[0])
    if last is None or base is None:
        return 0
    return max(0, (last - base).days)


def _ach_day_int(d, key):
    try:
        return int((d or {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _ach_hour_stats(rec):
    """(макс. сессий за один час, макс. разных часов в одном дне,
    макс. часов ПОДРЯД в одном дне).

    spread — сколько разных часов в сутках были рабочими (с дырками),
    run — самая длинная непрерывная цепочка часов. Это разные величины:
    отправки в 09 и в 23 дают spread=2, run=1."""
    best_hour = best_spread = best_run = 0
    for d in (rec.get("days") or {}).values():
        if not isinstance(d, dict):
            continue
        spread = run = 0
        for h in range(24):
            v = _ach_day_int(d, f"h{h}")
            if v > 0:
                spread += 1
                run += 1
                if v > best_hour:
                    best_hour = v
                if run > best_run:
                    best_run = run
            else:
                run = 0
        if spread > best_spread:
            best_spread = spread
    return best_hour, best_spread, best_run


def _ach_day_max(rec, field):
    best = 0
    for d in (rec.get("days") or {}).values():
        if isinstance(d, dict):
            v = _ach_day_int(d, field)
            if v > best:
                best = v
    return best


def _ach_day_streak(rec, field=None, flag=None):
    """Максимум идущих подряд дней, где field > 0 (или выставлен flag)."""
    days = rec.get("days") or {}
    keys = sorted(k for k in days if _ach_parse_day(k))
    best = cur = 0
    prev = None
    for k in keys:
        d = days.get(k) or {}
        ok = bool(d.get(flag)) if flag else _ach_day_int(d, field) > 0
        if not ok:
            cur = 0
            prev = None
            continue
        dt = _ach_parse_day(k)
        cur = cur + 1 if (prev is not None and (dt - prev).days == 1) else 1
        prev = dt
        if cur > best:
            best = cur
    return best


def _ach_skill90_streak(rec):
    days = rec.get("days") or {}
    best = cur = 0
    prev = None
    for k in sorted(k for k in days if _ach_parse_day(k)):
        v = (days.get(k) or {}).get("skill_last")
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v < ACH_SKILL_HI:
            cur = 0
            prev = None
            continue
        dt = _ach_parse_day(k)
        cur = cur + 1 if (prev is not None and (dt - prev).days == 1) else 1
        prev = dt
        if cur > best:
            best = cur
    return best


def _ach_month_days_max(rec):
    """Больше всего активных дней внутри одного календарного месяца."""
    months = {}
    for k, d in (rec.get("days") or {}).items():
        if not isinstance(d, dict) or _ach_day_int(d, "sessions") <= 0:
            continue
        if _ach_parse_day(k) is None:
            continue
        months[k[:7]] = months.get(k[:7], 0) + 1
    return max(months.values()) if months else 0


def _ach_partner_best(rec):
    best = 0
    for v in (rec.get("partner_counts") or {}).values():
        try:
            v = int(v or 0)
        except (TypeError, ValueError):
            continue
        if v > best:
            best = v
    return best


def _ach_partner_best_uid(rec):
    """Возвращает (uid_партнёра, кол-во) для самой частой пары — или (None, 0)."""
    pc = rec.get("partner_counts") or {}
    best_uid, best_n = None, 0
    for k, v in pc.items():
        try:
            n = int(v or 0)
        except (TypeError, ValueError):
            continue
        if n > best_n:
            try:
                best_uid = int(k)
            except (TypeError, ValueError):
                continue
            best_n = n
    return best_uid, best_n


def _ach_partner_best_line(rec, count):
    """Строка "Имя — N" для личной статистики самого участника: это его собственные
    данные о себе, поэтому имя партнёра показывается честно, даже если тот скрыт
    из топа — сокрытие касается видимости для ДРУГИХ, а не своей же статистики."""
    if not count:
        return "0"
    p_uid, _n = _ach_partner_best_uid(rec)
    if p_uid is None:
        return str(count)
    d = user_data.get(p_uid) or {}
    name = d.get("name") or str(p_uid)
    return f"{html.escape(str(name))} — {count}"


def _ach_tenure_days(rec):
    first = _ach_parse_day(rec.get("first_day") or "")
    if first is None:
        return 0
    keys = sorted(k for k in (rec.get("days") or {}) if _ach_parse_day(k))
    last = _ach_parse_day(keys[-1]) if keys else None
    if last is None or last < first:
        return 0
    return (last - first).days + 1


def _ach_metrics(rec):
    c = rec.get("counters") or {}
    days = rec.get("days") or {}
    sk = rec.get("skill") or {}
    st = rec.get("streak") or {}

    def _i(src, key):
        try:
            return int((src or {}).get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    day_sessions = [_i(d, "sessions") for d in days.values() if isinstance(d, dict)]
    try:
        peak = float(sk.get("peak", 0) or 0)
    except (TypeError, ValueError):
        peak = 0.0

    bests = rec.get("bests")
    if not isinstance(bests, dict):
        bests = {}
        rec["bests"] = bests

    def _best(key, val):
        """Рекорд за всё время: обрезка старых дней не должна откатывать прогресс."""
        try:
            prev = int(bests.get(key, 0) or 0)
        except (TypeError, ValueError):
            prev = 0
        val = int(val or 0)
        if val > prev:
            bests[key] = val
            return val
        return prev

    hour_max, day_hours, day_hours_run = _ach_hour_stats(rec)

    return {
        "sessions_total": _i(c, "sessions"),
        "urls_total": _i(c, "urls"),
        "full_streak": _i(c, "full_best"),
        "sessions_month": _best("sessions_month", _ach_window_max(rec, "sessions", 30)),
        "packs": _i(c, "packs"),
        "sessions_hour": _best("sessions_hour", hour_max),
        "day_hours": _best("day_hours", day_hours),
        "day_hours_run": _best("day_hours_run", day_hours_run),
        "hours_seen": len(rec.get("hours") or []),
        "weeks_active": _best("weeks_active", _ach_weeks_streak(rec, 1)),
        "month_days": _best("month_days", _ach_month_days_max(rec)),
        "vote_streak_days": _best("vote_streak_days", _ach_day_streak(rec, field="votes")),
        "votes_day": _best("votes_day", _ach_day_max(rec, "votes")),
        "lightning_votes": _i(c, "lightning_votes"),
        "skill_ups": _i(c, "skill_ups"),
        "skill90_streak": _best("skill90_streak", _ach_skill90_streak(rec)),
        "matches_day": _best("matches_day", _ach_day_max(rec, "matches")),
        "matches_burst": _i(c, "burst_best"),
        "partner_best": _best("partner_best", _ach_partner_best(rec)),
        "weekend_days": _i(c, "weekend_days"),
        "deep_night_days": _i(c, "deep_night_days"),
        "night_streak": _best("night_streak", _ach_day_streak(rec, flag="night")),
        "active_days": _i(c, "active_days"),
        "tenure_days": _best("tenure_days", _ach_tenure_days(rec)),
        "sessions_day": _best("sessions_day", max(day_sessions) if day_sessions else 0),
        "sessions_week": _best("sessions_week", _ach_window_max(rec, "sessions", 7)),
        "full_packs": _i(c, "full_packs"),
        "streak_days": _i(st, "best"),
        "weeks_100": _best("weeks_100", _ach_weeks_streak(rec, ACH_WEEK_GOAL)),
        "votes_total": _i(c, "votes"),
        "votes_2d": _best("votes_2d", _ach_window_max(rec, "votes", 2)),
        "final_votes": _i(c, "final_votes"),
        "fast_votes": _i(c, "fast_votes"),
        "skill_peak": peak,
        "skill_hi_days": _i(sk, "hi_days"),
        "skill_nofall": _best("skill_nofall", _ach_skill_nofall(rec)),
        "comebacks": _i(c, "comebacks"),
        "matches_total": _i(c, "matches"),
        "partners_unique": len(rec.get("partners") or []),
        "night_days": max(_i(c, "night_days"),
                          sum(1 for d in days.values() if isinstance(d, dict) and d.get("night"))),
        "early_days": max(_i(c, "early_days"),
                          sum(1 for d in days.values() if isinstance(d, dict) and d.get("early"))),
        "clean_streak": _i(c, "clean_best"),
        "no_undo_days": _ach_no_undo_days(rec),
    }


def _ach_scan(uid):
    """Проверяет все пороги. Возвращает список сработавших тиров."""
    rec = _ach_rec(uid)
    m = _ach_metrics(rec)
    fired = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for a in ACHIEVEMENTS_DEF:
        val = m.get(a["metric"], 0)
        for ti, need in enumerate(a["tiers"]):
            key = _ach_unlock_key(a["code"], need)
            if key in rec["unlocked"]:
                continue
            if val >= need:
                rec["unlocked"][key] = now
                fired.append({"a": a, "tier": ti, "xp": _ach_tier_xp(a, ti), "val": val})
    _ach_prune(rec)
    prev_xp = int(rec.get("xp", 0) or 0)
    new_xp = _ach_xp(rec)
    if fired or new_xp != prev_xp:
        save_achievements()
    return fired


def _ach_fmt_val(v):
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return f"{v:.1f}".replace(".", ",")
    return str(v)


def _ach_bar(cur, need, width=10):
    try:
        cur = float(cur)
        need = float(need)
    except (TypeError, ValueError):
        return "░" * width
    if need <= 0:
        return "▓" * width
    filled = int(round(width * min(1.0, max(0.0, cur / need))))
    return "▓" * filled + "░" * (width - filled)


def _ach_tier_icon(a, ti):
    if a.get("hidden"):
        return "🕵️"
    return ACH_TIER_ICONS[min(ti, len(ACH_TIER_ICONS) - 1)]


def _ach_tier_name(a, ti):
    if a.get("hidden"):
        return "Скрытая"
    return ACH_TIER_NAMES[min(ti, len(ACH_TIER_NAMES) - 1)]


def _ach_level(xp):
    """Возвращает (уровень, xp внутри уровня, сколько нужно на уровень)."""
    try:
        xp = int(xp or 0)
    except (TypeError, ValueError):
        xp = 0
    lvl, step, base = 1, 100, 0
    while True:
        need = step
        if xp - base < need:
            return lvl, xp - base, need
        base += need
        lvl += 1
        step += 50


def _ach_state(uid):
    """Сводка по юзеру для экранов: список ачивок с текущим тиром и прогрессом."""
    rec = _ach_rec(uid)
    m = _ach_metrics(rec)
    items = []
    for a in ACHIEVEMENTS_DEF:
        val = m.get(a["metric"], 0)
        done, nxt = 0, None
        for ti, need in enumerate(a["tiers"]):
            if _ach_unlock_key(a["code"], need) in rec["unlocked"]:
                done += 1
            elif nxt is None:
                nxt = need
        items.append({
            "a": a, "val": val, "done": done,
            "next": nxt, "max": len(a["tiers"]),
        })
    return rec, m, items


def _ach_nearest(items, limit=3):
    live = [i for i in items
            if i["next"] is not None and not i["a"].get("hidden")]
    live.sort(key=lambda i: -(float(i["val"]) / float(i["next"]) if i["next"] else 0))
    return live[:limit]


def _ach_unlock_block(uid, fired):
    """Одно сообщение на пачку разблокировок — без спама по сообщению на тир."""
    rec, m, items = _ach_state(uid)
    lvl, cur_xp, need_xp = _ach_level(_ach_xp(rec))
    gained = sum(f["xp"] for f in fired)
    # несколько тиров одной ачивки схлопываем в одну строку — показываем верхний
    merged = {}
    for f in fired:
        code = f["a"]["code"]
        prev = merged.get(code)
        if prev is None or f["tier"] > prev["tier"]:
            merged[code] = dict(f)
        merged[code]["xp"] = (prev["xp"] if prev else 0) + f["xp"]
    fired = list(merged.values())
    tail = (f"\n⭐ Уровень {lvl} • +{gained} XP • всего {rec.get('xp', 0)}\n"
            f"{_ach_bar(cur_xp, need_xp)} {cur_xp}/{need_xp} до {lvl + 1} уровня")

    if len(fired) == 1:
        f = fired[0]
        a, ti = f["a"], f["tier"]
        it = next((i for i in items if i["a"]["code"] == a["code"]), None)
        text = (f"🏆 <b>НОВОЕ ДОСТИЖЕНИЕ</b>\n\n"
                f"{_ach_tier_icon(a, ti)} <b>{html.escape(a['name'])}</b> — "
                f"{html.escape(_ach_tier_name(a, ti))}\n"
                f"{html.escape(a['desc'])}: {_ach_fmt_val(f['val'])}\n")
        if it and it["next"] is not None:
            text += (f"\nСледующий тир: {_ach_fmt_val(it['next'])} "
                     f"{html.escape(a.get('unit') or '')}\n"
                     f"{_ach_bar(it['val'], it['next'])} "
                     f"{_ach_fmt_val(it['val'])}/{_ach_fmt_val(it['next'])}\n").rstrip() + "\n"
        else:
            text += "\n💎 Все тиры этой ачивки взяты.\n"
        return [text + tail + "\n\n"]

    lines = [f"🏆 <b>СРАЗУ {len(fired)} ДОСТИЖЕНИЯ</b>", ""]
    for f in fired:
        a, ti = f["a"], f["tier"]
        lines.append(f"{_ach_tier_icon(a, ti)} <b>{html.escape(a['name'])}</b> — "
                     f"{html.escape(_ach_tier_name(a, ti))} • +{f['xp']} XP")
        lines.append(f"   {html.escape(a['desc'])}: {_ach_fmt_val(f['val'])}")
    near = _ach_nearest(items, 1)
    if near:
        i = near[0]
        lines.append("")
        lines.append(f"Дальше: {html.escape(i['a']['name'])} "
                     f"{_ach_bar(i['val'], i['next'])} "
                     f"{_ach_fmt_val(i['val'])}/{_ach_fmt_val(i['next'])}")
    return ["\n".join(lines) + "\n" + tail + "\n\n"]


def _ach_is_optout(uid):
    """Скрытость: не участвует в топе, чужой профиль недоступен. Данные и XP всё равно копятся."""
    return bool((user_data.get(uid, {}) or {}).get("ach_optout"))


async def ach_award(bot, uid):
    """Проверить пороги и уведомить. Работает и в режиме скрытости — данные и ачивки
    не должны теряться, пока пользователь не участвует в топе/просмотре. Никогда не роняет вызывающий код."""
    try:
        fired = _ach_scan(uid)
    except Exception as e:
        logger.warning(f"Ачивки: ошибка проверки ({uid}): {e}")
        return
    if not fired:
        return
    if not (user_data.get(uid, {}) or {}).get("ach_notify", True):
        return
    try:
        await send_blocks(bot, uid, _ach_unlock_block(uid, fired))
    except Exception as e:
        logger.warning(f"Ачивки: не удалось отправить уведомление {uid}: {e}")


# ---------- события ----------

def ach_on_links(uid, added_count, batch_size, at_str=None):
    if added_count <= 0 and batch_size <= 0:
        return
    rec = _ach_rec(uid)
    dt = _parse_added_at(at_str) if at_str else None
    day = _ach_day_key(uid, dt)
    local = _ach_local_dt(uid, dt)
    if added_count > 0:
        # одна отправленная пачка (1-5 ссылок) = одна выполненная сессия
        _ach_bump(rec, "sessions", 1)
        _ach_day_bump(rec, day, "sessions", 1)
        _ach_bump(rec, "urls", added_count)
        _ach_day_bump(rec, day, "urls", added_count)
        st = rec["streak"]
        last = st.get("last_day") or ""
        prev = _ach_parse_day(last)
        cur_day = _ach_parse_day(day)
        if last != day and not (prev and cur_day and cur_day < prev):
            # задним числом серию не пересчитываем — только вперёд
            if prev and cur_day and (cur_day - prev).days == 1:
                st["cur"] = int(st.get("cur", 0) or 0) + 1
            else:
                st["cur"] = 1
            st["last_day"] = day
            st["best"] = max(int(st.get("best", 0) or 0), int(st["cur"]))
        d = _ach_day(rec, day)
        if 0 <= local.hour < 5:
            if not d.get("night"):
                _ach_bump(rec, "night_days", 1)
            d["night"] = 1
        elif 5 <= local.hour < 7:
            if not d.get("early"):
                _ach_bump(rec, "early_days", 1)
            d["early"] = 1
        if 3 <= local.hour < 5 and not d.get("deep_night"):
            d["deep_night"] = 1
            _ach_bump(rec, "deep_night_days", 1)
        if local.weekday() >= 5 and not d.get("wknd"):
            d["wknd"] = 1
            _ach_bump(rec, "weekend_days", 1)
        if not d.get("counted_day"):
            d["counted_day"] = 1
            _ach_bump(rec, "active_days", 1)
        _ach_day_bump(rec, day, f"h{local.hour}", 1)
        hours = rec["hours"]
        if local.hour not in hours:
            hours.append(local.hour)
        first = rec.get("first_day")
        if not first or day < str(first):
            rec["first_day"] = day
        _ach_bump(rec, "packs", 1)
    if batch_size >= MAX_LINKS:
        _ach_bump(rec, "full_packs", 1)
        full = _ach_bump(rec, "full_streak", 1)
        rec["counters"]["full_best"] = max(
            int(rec["counters"].get("full_best", 0) or 0), full)
    else:
        rec["counters"]["full_streak"] = 0
    clean = _ach_bump(rec, "clean_streak", 1)
    rec["counters"]["clean_best"] = max(
        int(rec["counters"].get("clean_best", 0) or 0), clean)
    _ach_prune(rec)
    save_achievements()


def ach_on_undo(uid, removed_count, at_str=None):
    rec = _ach_rec(uid)
    dt = _parse_added_at(at_str) if at_str else None
    day = _ach_day_key(uid, dt)
    if removed_count > 0:
        _ach_bump(rec, "sessions", -1)
        _ach_day_bump(rec, day, "sessions", -1)
        _ach_bump(rec, "urls", -removed_count)
        _ach_day_bump(rec, day, "urls", -removed_count)
    rec["counters"]["clean_streak"] = 0
    _ach_bump(rec, "undo_total", 1)
    rec["last_undo_day"] = _ach_day_key(uid)
    save_achievements()


def ach_on_skill(uid, value):
    try:
        val = float(value)
    except (TypeError, ValueError):
        return
    rec = _ach_rec(uid)
    day = _ach_day_key(uid)
    d = _ach_day(rec, day)
    d["skill_last"] = val
    d["skill_n"] = int(d.get("skill_n", 0) or 0) + 1
    prev_min = d.get("skill_min")
    d["skill_min"] = val if prev_min is None else min(float(prev_min), val)
    prev_max = d.get("skill_max")
    d["skill_max"] = val if prev_max is None else max(float(prev_max), val)

    sk = rec["skill"]
    prev_val = sk.get("last")
    if prev_val is not None:
        try:
            if val > float(prev_val):
                _ach_bump(rec, "skill_ups", 1)
        except (TypeError, ValueError):
            pass
    sk["last"] = val
    try:
        peak = float(sk.get("peak", 0) or 0)
    except (TypeError, ValueError):
        peak = 0.0
    sk["peak"] = max(peak, val)

    if val <= ACH_SKILL_LOW:
        sk["below"] = True
    elif val >= ACH_SKILL_HI and sk.get("below"):
        sk["below"] = False
        _ach_bump(rec, "comebacks", 1)

    # «весь день 90+» засчитывается один раз на день и только при 2+ замерах
    if (d.get("skill_n", 0) >= ACH_SKILL_MIN_SAMPLES
            and float(d.get("skill_min", 0) or 0) >= ACH_SKILL_HI
            and not d.get("hi_counted")):
        d["hi_counted"] = 1
        sk["hi_days"] = int(sk.get("hi_days", 0) or 0) + 1
    elif d.get("hi_counted") and float(d.get("skill_min", 0) or 0) < ACH_SKILL_HI:
        # навык просел позже в тот же день — день перестаёт считаться
        d.pop("hi_counted", None)
        sk["hi_days"] = max(0, int(sk.get("hi_days", 0) or 0) - 1)

    _ach_prune(rec)
    save_achievements()


def ach_on_vote(uid, is_final, created_at=None):
    """Возвращает, что именно было зачислено — нужно для точного отката
    (ach_on_vote_undo), если матч потом инвалидируется отменой отправки."""
    rec = _ach_rec(uid)
    day = _ach_day_key(uid)
    credit = {"votes": 1, "final_votes": 0, "fast_votes": 0, "lightning_votes": 0}
    _ach_bump(rec, "votes", 1)
    _ach_day_bump(rec, day, "votes", 1)
    if is_final:
        _ach_bump(rec, "final_votes", 1)
        credit["final_votes"] = 1
    started = _parse_added_at(created_at) if created_at else None
    if started:
        delta = (datetime.now() - started).total_seconds()
        if 0 <= delta <= ACH_FAST_VOTE_SECONDS:
            _ach_bump(rec, "fast_votes", 1)
            credit["fast_votes"] = 1
        if 0 <= delta <= ACH_LIGHTNING_SECONDS:
            _ach_bump(rec, "lightning_votes", 1)
            credit["lightning_votes"] = 1
    _ach_prune(rec)
    save_achievements()
    return credit


def ach_on_vote_undo(uid, credit):
    """Обратная к ach_on_vote — снимает ровно то, что было начислено этим
    конкретным голосом (votes/final_votes/fast_votes/lightning_votes),
    когда матч, к которому голос относился, инвалидирован отменой отправки.
    День намеренно не трогаем — как и с matches, дневные рекорды в этой
    системе принципиально только растут (см. _best())."""
    if not credit:
        return
    rec = _ach_rec(uid)
    c = rec["counters"]
    for key in ("votes", "final_votes", "fast_votes", "lightning_votes"):
        n = int(credit.get(key, 0) or 0)
        if n <= 0:
            continue
        try:
            cur = int(c.get(key, 0) or 0)
        except (TypeError, ValueError):
            cur = 0
        c[key] = max(0, cur - n)
    save_achievements()


def ach_on_matches(uid, count, partner_uids=()):
    if count <= 0 and not partner_uids:
        return
    rec = _ach_rec(uid)
    if count > 0:
        _ach_bump(rec, "matches", count)
        _ach_day_bump(rec, _ach_day_key(uid), "matches", count)
        try:
            best = int(rec["counters"].get("burst_best", 0) or 0)
        except (TypeError, ValueError):
            best = 0
        rec["counters"]["burst_best"] = max(best, int(count))
    partners = rec["partners"]
    pc = rec["partner_counts"]
    changed = False
    for p in partner_uids:
        if p == uid:
            continue
        key = str(p)
        if key not in partners:
            partners.append(key)
            changed = True
        try:
            pc[key] = int(pc.get(key, 0) or 0) + 1
        except (TypeError, ValueError):
            pc[key] = 1
        changed = True
    if count > 0 or changed:
        _ach_prune(rec)
        save_achievements()


def ach_on_matches_undo(uid, count, partner_uids=()):
    """Обратная операция к ach_on_matches — снимает зачёт ровно одного совпадения
    (и связанных партнёров), когда матч оказался инвалидирован отменой отправки.
    Никогда не уходит в минус. Дневной бакет matches намеренно не трогаем —
    он влияет только на 'рекорд за день', а рекорды в этой системе принципиально
    никогда не понижаются (см. _best())."""
    if count <= 0 and not partner_uids:
        return
    rec = _ach_rec(uid)
    if count > 0:
        c = rec["counters"]
        try:
            cur = int(c.get("matches", 0) or 0)
        except (TypeError, ValueError):
            cur = 0
        c["matches"] = max(0, cur - count)
    partners = rec["partners"]
    pc = rec["partner_counts"]
    changed = False
    for p in partner_uids:
        if p == uid:
            continue
        key = str(p)
        try:
            new_v = max(0, int(pc.get(key, 0) or 0) - 1)
        except (TypeError, ValueError):
            new_v = 0
        if new_v > 0:
            pc[key] = new_v
        else:
            pc.pop(key, None)
            if key in partners:
                partners.remove(key)
        changed = True
    if count > 0 or changed:
        _ach_prune(rec)
        save_achievements()


def ach_vote_counted(state, target_key, is_final, undo=False):
    """Один зачёт на связку токен+участник+фаза. Отмена оценки снимает зачёт."""
    marks = state.get("ach_counted")
    if not isinstance(marks, dict):
        marks = {}
        state["ach_counted"] = marks
    phase = "final" if is_final else "init"
    lst = marks.get(phase)
    if not isinstance(lst, list):
        lst = []
        marks[phase] = lst
    key = str(target_key)
    if undo:
        if key in lst:
            lst.remove(key)
        return False
    if key in lst:
        return False
    lst.append(key)
    return True


achievements: Dict[int, dict] = load_achievements()


def _ach_migrate_all() -> int:
    """Разовый прогон при старте: единицы v1->v2, ключи тиров, пересчёт XP."""
    changed = 0
    for uid in list(achievements.keys()):
        try:
            rec = achievements.get(uid)
            before = int((rec or {}).get("v", 1) or 1) if isinstance(rec, dict) else 1
            rec = _ach_rec(uid)
            prev_xp = int(rec.get("xp", 0) or 0)
            if before < ACH_UNITS_VERSION or _ach_xp(rec) != prev_xp:
                changed += 1
        except Exception as e:
            logger.warning(f"Ачивки: не удалось мигрировать {uid}: {e}")
    if changed:
        save_achievements()
        logger.info(f"Ачивки: пересчитано записей — {changed}")
    return changed


_ach_migrate_all()


def _ach_fix_partner_symmetry() -> int:
    """Разовая починка уже сохранённой статистики после бага рассинхрона зачёта
    совпадений (partner_counts мог занижаться неравномерно на разных сторонах
    пары — см. фикс в _ach_credit_matches). Пара всегда взаимна: сколько раз
    A совпал с B, столько же раз B совпал с A. Поднимаем ОБЕ стороны до
    максимума из: текущего значения A, текущего значения B, и того, что ещё
    живо в match_votes (самый надёжный источник, но он режется TTL, поэтому
    только подстраховка, а не единственный источник). Значения только
    поднимаются, никогда не занижаются — не теряем то, что уже насчитано."""
    changed = 0
    real_pair = Counter()
    for st in (match_votes or {}).values():
        if not isinstance(st, dict):
            continue
        uids_m = [u.get("uid") for u in (st.get("users") or []) if isinstance(u, dict)]
        uids_m = [u for u in uids_m if u is not None]
        if len(uids_m) < 2:
            continue
        for a in uids_m:
            for b in uids_m:
                if a != b:
                    real_pair[(a, b)] += 1

    for uid in list(achievements.keys()):
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            continue
        rec = _ach_rec(uid_int)
        pc = rec.get("partner_counts")
        if not isinstance(pc, dict):
            continue
        for key in list(pc.keys()):
            try:
                p_uid = int(key)
                a = int(pc.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            other_rec = _ach_rec(p_uid)
            other_pc = other_rec.setdefault("partner_counts", {})
            try:
                b = int(other_pc.get(str(uid_int), 0) or 0)
            except (TypeError, ValueError):
                b = 0
            truth = max(a, b, real_pair.get((uid_int, p_uid), 0))
            if truth > a:
                pc[key] = truth
                changed += 1
            if truth > b:
                other_pc[str(uid_int)] = truth
                changed += 1
    if changed:
        save_achievements()
        logger.info(f"Ачивки: починена симметрия пар — исправлено записей {changed}")
    return changed


_ach_fix_partner_symmetry()


def _ach_fix_vote_pair_misattribution() -> int:
    """Разовая починка после бага в vote_callback: клик кросс-войсера (VOTE_PAIRS)
    записывал голос на target_uid, а зачёт ачивки уходил тому, кто физически нажал
    кнопку — то есть партнёру по паре. В паре Pavel/Элис голоса Элис годами
    записывались Павлу (ach_vote_counted гейтит по target_uid, а ach_on_vote
    зачислял по uid — см. фикс в vote_callback).

    Кто именно кликал по каждому историческому голосу — нигде не хранилось
    (сохранялся только target_uid), поэтому 1-в-1 по всей истории не восстановить.
    Но в ещё живом match_votes.json отметки ach_counted хранят ПРАВИЛЬНОГО
    target_uid — берём это как нижнюю границу правды и переносим дефицит от
    партнёра с избытком, не уходя у него в минус ниже его же нижней границы и
    ничего не выдумывая сверху."""
    if not VOTE_PAIRS or not match_votes:
        return 0
    changed = 0
    window_init, window_final = Counter(), Counter()
    for st in match_votes.values():
        if not isinstance(st, dict):
            continue
        ac = st.get("ach_counted") or {}
        for k in (ac.get("init") or []):
            window_init[k] += 1
        for k in (ac.get("final") or []):
            window_final[k] += 1

    for a_uid, b_uid in VOTE_PAIRS:
        for lo, hi in ((a_uid, b_uid), (b_uid, a_uid)):
            lo_key, hi_key = str(lo), str(hi)
            need_total = window_init[lo_key] + window_final[lo_key]
            need_final = window_final[lo_key]
            c_lo = _ach_rec(lo)["counters"]
            c_hi = _ach_rec(hi)["counters"]
            cur_lo = int(c_lo.get("votes", 0) or 0)
            deficit = max(0, need_total - cur_lo)
            if deficit:
                hi_floor = window_init[hi_key] + window_final[hi_key]
                movable = max(0, int(c_hi.get("votes", 0) or 0) - hi_floor)
                move = min(deficit, movable)
                if move:
                    c_hi["votes"] = int(c_hi.get("votes", 0) or 0) - move
                    c_lo["votes"] = cur_lo + move
                    changed += 1
            cur_lo_final = int(c_lo.get("final_votes", 0) or 0)
            deficit_final = max(0, need_final - cur_lo_final)
            if deficit_final:
                hi_floor_f = window_final[hi_key]
                movable_f = max(0, int(c_hi.get("final_votes", 0) or 0) - hi_floor_f)
                move_f = min(deficit_final, movable_f)
                if move_f:
                    c_hi["final_votes"] = int(c_hi.get("final_votes", 0) or 0) - move_f
                    c_lo["final_votes"] = cur_lo_final + move_f
                    changed += 1
    if changed:
        save_achievements()
        logger.info(f"Ачивки: починена атрибуция голосов VOTE_PAIRS — записей {changed}")
    return changed


_ach_fix_vote_pair_misattribution()


def _ach_rescan_after_retrofix() -> int:
    """Ретро-фиксы выше правят только сырые counters — тиры/бейджи/XP считаются
    отдельно (_ach_xp читает только rec['unlocked'], который обновляет только
    _ach_scan на реальном событии). Без этого прогона починенные цифры могли бы
    молча пересечь порог тира, и никто бы не увидел ни бейджа, ни уведомления,
    ни правильного XP, пока человек не отправит что-то ещё сам. Тиры отсюда
    только добавляются (никогда не снимаются) — как и везде в этой системе."""
    total_fired = 0
    for uid_s in list(achievements.keys()):
        try:
            uid = int(uid_s)
        except (TypeError, ValueError):
            continue
        try:
            fired = _ach_scan(uid)
            total_fired += len(fired)
        except Exception as e:
            logger.warning(f"Ачивки: рескан после ретро-фикса {uid}: {e}")
    if total_fired:
        logger.info(f"Ачивки: рескан после ретро-фикса — новых тиров {total_fired}")
    return total_fired


_ach_rescan_after_retrofix()


def load_access():
    data = _load_json(ACCESS_FILE, None)
    if not isinstance(data, dict):
        return {}
    result = {}
    for k, v in data.items():
        try:
            uid = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict):
            result[uid] = v
    return result


def save_access():
    return _save_json(ACCESS_FILE, access_data)


access_data: Dict[int, dict] = load_access()


def _seed_access() -> int:
    """Первый запуск: выдать доступ админам, рассыльщикам и всем уже зарегистрированным."""
    added = 0
    now = datetime.now().isoformat()
    for uid in list(ADMIN_IDS) + list(BROADCAST_IDS):
        if uid not in access_data:
            access_data[uid] = {"status": "approved", "decided_by": "seed:config", "decided_at": now}
            added += 1
    for uid, d in user_data.items():
        if uid in access_data or not isinstance(d, dict) or not d.get("registered"):
            continue
        access_data[uid] = {"status": "approved", "decided_by": "seed:registered",
                            "decided_at": now, "name": d.get("name"),
                            "username": d.get("username")}
        added += 1
    if added:
        save_access()
        logger.info(f"🔐 Белый список создан: {added} участников получили доступ автоматом")
    return added


_access_seeded = _seed_access()


def access_status(uid) -> str:
    try:
        rec = access_data.get(int(uid))
    except (TypeError, ValueError):
        return "unknown"
    if not isinstance(rec, dict):
        return "unknown"
    st = rec.get("status")
    return st if st in ("approved", "pending", "denied") else "unknown"


def is_approved(uid) -> bool:
    if not ACCESS_CONTROL:
        return True
    if uid in ADMIN_IDS:
        return True
    return access_status(uid) == "approved"


def set_access(uid, status, decided_by=None, user=None) -> None:
    uid = int(uid)
    rec = access_data.get(uid) or {}
    rec["status"] = status
    rec["decided_at"] = datetime.now().isoformat()
    if decided_by is not None:
        rec["decided_by"] = str(decided_by)
    if user is not None:
        if getattr(user, "username", None):
            rec["username"] = user.username
        name = getattr(user, "full_name", None) or getattr(user, "first_name", None)
        if name:
            rec["name"] = name
    access_data[uid] = rec
    save_access()


def access_users(status: str) -> list:
    return sorted(uid for uid in access_data if access_status(uid) == status)


def _access_display(uid) -> str:
    uid = int(uid)
    rec = access_data.get(uid, {}) if isinstance(access_data.get(uid), dict) else {}
    prof = user_data.get(uid, {}) if isinstance(user_data.get(uid), dict) else {}
    name = rec.get("name") or prof.get("name") or str(uid)
    uname = rec.get("username") or prof.get("username")
    href = mention_href(uid, uname)
    tag = f" (@{html.escape(str(uname))})" if uname else ""
    return f'<a href="{href}">{html.escape(str(name))}</a>{tag} — <code>{uid}</code>'
_vote_seq = max((int(k) for k in match_votes if k.isdigit()), default=0)
last_check_time = {}


NOTIFY_ON_BTN = "🔔 Уведомления: вкл"
NOTIFY_OFF_BTN = "🔕 Уведомления: выкл"
SKILL_ON_BTN = "📈 Навык: вкл"
SKILL_OFF_BTN = "📉 Навык: выкл"
AUTO_ON_BTN = "⚡ Автопроверка: вкл"
AUTO_OFF_BTN = "🐢 Автопроверка: выкл"

BASE_COMMANDS = [
    ("start", "Запуск и регистрация"),
    ("myid", "Мой ID для расширения"),
    ("undo", "Отменить последнюю отправку"),
    ("change_name", "Сменить имя"),
    ("tz", "Часовой пояс"),
    ("ach", "Достижения и статистика"),
]
SKILL_OFF_COMMAND = ("skill_off", "Выключить уведомления о навыке")
SKILL_ON_COMMAND = ("skill_on", "Включить уведомления о навыке")

MENU_BUTTONS = [
    "📋 Мои ссылки", "🔥 Актуальные сессии", "👥 Участники", "✅ Проверить совпадения",
    "✏️ Сменить имя", "🔄 Новая сессия", "↩️ Отменить отправку", ACH_BTN,
    NOTIFY_ON_BTN, NOTIFY_OFF_BTN,
    SKILL_ON_BTN, SKILL_OFF_BTN,
    AUTO_ON_BTN, AUTO_OFF_BTN,
    "📣 Рассылка", "🧽 Очистить старое", "💥 Полный сброс",
]
ADMIN_BUTTONS = ["📣 Рассылка", "🧽 Очистить старое", "💥 Полный сброс"]
ALL_BUTTONS = MENU_BUTTONS + ["🚀 Начать работу", "❌ Отмена"]


def is_admin(user_id) -> bool:
    return user_id in ADMIN_IDS


def can_broadcast(user_id) -> bool:
    return user_id in ADMIN_IDS or user_id in BROADCAST_IDS


def get_main_keyboard(user_id=None):
    notify_on = True
    if user_id is not None and user_id in user_data:
        notify_on = user_data[user_id].get("notify", True)
    notify_btn = NOTIFY_ON_BTN if notify_on else NOTIFY_OFF_BTN

    skill_on = True
    if user_id is not None and user_id in user_data:
        skill_on = user_data[user_id].get("skill_notify", True)
    skill_btn = SKILL_ON_BTN if skill_on else SKILL_OFF_BTN

    auto_on = AUTO_CHECK_DEFAULT
    if user_id is not None and user_id in user_data:
        auto_on = user_data[user_id].get("auto_check", AUTO_CHECK_DEFAULT)
    auto_btn = AUTO_ON_BTN if auto_on else AUTO_OFF_BTN

    # Новая раскладка: 3 ряда по 4 кнопки
    keyboard = [
        [
            KeyboardButton("📋 Мои ссылки"),
            KeyboardButton("👥 Участники"),
            KeyboardButton("🔥 Актуальные сессии"),
            KeyboardButton("✅ Проверить совпадения"),
        ],
        [
            KeyboardButton("🔄 Новая сессия"),
            KeyboardButton("↩️ Отменить отправку"),
            KeyboardButton(auto_btn),
            KeyboardButton(ACH_BTN),
        ],
        [
            KeyboardButton(skill_btn),
            KeyboardButton("✏️ Сменить имя"),
            KeyboardButton(notify_btn),
        ],
    ]

    # Кнопки для администраторов и рассыльщиков
    extra_buttons = []
    if can_broadcast(user_id):
        extra_buttons.append("📣 Рассылка")
    if is_admin(user_id):
        extra_buttons.append("🧽 Очистить старое")
        extra_buttons.append("💥 Полный сброс")

    if extra_buttons:
        # Добавляем в новый ряд, разбивая по 4 кнопки
        row = []
        for btn in extra_buttons:
            row.append(KeyboardButton(btn))
            if len(row) == 4:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def is_valid_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if len(url) > 2000 or len(url) < 8:
        return False
    if not (url.startswith('http://') or url.startswith('https://')):
        return False
    lowered = url.lower()
    if '<' in url or '>' in url or '"' in url or "'" in url:
        return False
    if 'javascript:' in lowered or 'data:text/html' in lowered or '<script' in lowered:
        logger.warning(f"Потенциально опасная ссылка: {url[:100]}")
        return False
    return True


def clean_url(url: str) -> str:
    url = url.strip()
    url = re.sub(r'[.,;:!?)]+$', '', url)
    return url


def _parse_link_entries(text: str):
    entries = []
    seen = {}

    def _add(session, yang):
        if session in seen:
            if yang and not entries[seen[session]][1]:
                entries[seen[session]] = (session, yang)
            return
        seen[session] = len(entries)
        entries.append((session, yang))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if LINK_YANG_SEP in line:
            left, _, right = line.partition(LINK_YANG_SEP)
            session = clean_url(left.strip())
            yang = clean_url(right.strip())
            if is_valid_url(session):
                _add(session, yang if is_valid_url(yang) else None)
        else:
            for part in line.split():
                cp = clean_url(part)
                if is_valid_url(cp):
                    _add(cp, None)
    return entries


def _safe_cut(line: str, limit: int) -> int:
    cut = min(limit, len(line))
    head = line[:cut]
    lt = head.rfind("<")
    if lt != -1 and head.find(">", lt) == -1:
        cut = lt
    head = line[:cut]
    amp = head.rfind("&")
    if amp != -1 and head.find(";", amp) == -1 and cut - amp <= 10:
        cut = amp
    return max(1, cut)


def _split_oversized_block(block: str) -> list:
    lines = block.split("\n")
    parts = []
    buf = ""
    for line in lines:
        while len(line) > SAFE_LIMIT:
            if buf:
                parts.append(buf)
                buf = ""
            cut = _safe_cut(line, SAFE_LIMIT)
            parts.append(line[:cut])
            line = line[cut:]
        if len(buf) + len(line) + 1 > SAFE_LIMIT:
            parts.append(buf)
            buf = line
        else:
            buf = buf + "\n" + line if buf else line
    if buf:
        parts.append(buf)
    return parts


def pack_blocks(blocks: list) -> list:
    messages = []
    current = ""
    for block in blocks:
        if len(block) > SAFE_LIMIT:
            if current:
                messages.append(current)
                current = ""
            messages.extend(_split_oversized_block(block))
            continue
        if len(current) + len(block) > SAFE_LIMIT:
            messages.append(current)
            current = block
        else:
            current += block
    if current:
        messages.append(current)
    return messages


async def send_blocks(bot, chat_id, blocks: list, parse_mode: str = "HTML"):
    messages = pack_blocks(blocks)
    total = len(messages)
    for i, msg in enumerate(messages, start=1):
        text = msg
        if total > 1:
            text = f"📄 Часть {i}/{total}\n\n" + text
        await tg(bot.send_message, chat_id=chat_id, text=text, parse_mode=parse_mode,
                               disable_web_page_preview=True)
    return total


def is_user_active(user_id: int) -> bool:
    if user_id not in user_data:
        return False
    if not user_data[user_id].get("registered"):
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    user_id = update.effective_user.id
    try:
        if user_id in user_data and user_data[user_id].get("registered"):
            name = user_data[user_id]["name"]
            links_count = len(user_data[user_id].get("links", {}))
            await tg(update.message.reply_text,
                f"👋 С возвращением, {name}!\n\n"
                f"📊 Твоя статистика:\n"
                f"• Добавлено ссылок: {links_count}/{MAX_LINKS}\n"
                f"• Статус: активен\n\n"
                f"Используй кнопки меню для работы.\n\n"
                f"💡 Чтобы сменить имя, отправь /change_name",
                reply_markup=get_main_keyboard(user_id)
            )
            return ConversationHandler.END
        await tg(update.message.reply_text,
            "👋 Привет! Добро пожаловать в бот для проверки ссылок!\n\n"
            "📌 Как это работает:\n"
            "1. Ты вводишь своё имя\n"
            f"2. Отправляешь ссылки (до {MAX_LINKS} штук)\n"
            "3. Бот сравнивает их с другими участниками\n"
            "4. Результат приходит в общий чат\n\n"
            "👉 Нажми «Начать работу»",
            reply_markup=get_start_keyboard()
        )
        return WAITING_FOR_NAME
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")
        await tg(update.message.reply_text, "❌ Произошла ошибка. Попробуй /start ещё раз")
        return ConversationHandler.END


async def start_work(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tg(update.message.reply_text,
        "Введи своё имя (как тебя называть):\n\n"
        f"📏 Имя должно быть не длиннее {MAX_NAME_LENGTH} символов",
        reply_markup=get_start_keyboard()
    )
    return WAITING_FOR_NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if text == "❌ Отмена":
        await tg(update.message.reply_text, "❌ Регистрация отменена.", reply_markup=get_start_keyboard())
        return ConversationHandler.END
    error = validate_name(text)
    if error:
        await tg(update.message.reply_text, error)
        return WAITING_FOR_NAME
    try:
        user_data[user_id] = {
            "name": text,
            "username": update.effective_user.username,
            "links": {},
            "registered": True,
            "notify": True,
            "skill_notify": True,
            "created_at": datetime.now().isoformat()
        }
        save_data()
        await _sync_skill_commands(context.bot, user_id, True)
        await tg(update.message.reply_text,
            f"🤝 Отлично, {text}!\n\n"
            f"Теперь отправь мне ссылки.\n\n"
            f"📌 До {MAX_LINKS} ссылок, каждая с новой строки:\n"
            f"https://example.com/1\n"
            f"https://example.com/2\n\n"
            f"✏️ Введи ссылки:\n\n"
            f"💡 Чтобы сменить имя позже, отправь /change_name",
            reply_markup=get_main_keyboard(user_id)
        )
        return WAITING_FOR_LINKS
    except Exception as e:
        logger.error(f"Ошибка сохранения имени: {e}")
        await tg(update.message.reply_text, "❌ Ошибка сервера. Попробуй /start позже")
        return ConversationHandler.END


async def change_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text, "❌ Ты ещё не зарегистрирован! Нажми /start", reply_markup=get_start_keyboard())
        return ConversationHandler.END
    if await _handle_skill_gate(update, context, update.message.text or ""):
        return WAITING_FOR_LINKS
    user_data[user_id]["awaiting_new_name"] = True
    save_data()
    await tg(update.message.reply_text,
        "✏️ Введи новое имя:\n\n"
        f"📏 Имя должно быть не длиннее {MAX_NAME_LENGTH} символов\n\n"
        "❌ Отмена — чтобы отменить",
        reply_markup=get_start_keyboard()
    )
    return WAITING_FOR_NEW_NAME


async def change_name_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text == "❌ Отмена":
        if isinstance(user_data.get(user_id), dict):
            user_data[user_id].pop("awaiting_new_name", None)
            save_data()
        await tg(update.message.reply_text, "❌ Смена имени отменена.", reply_markup=get_main_keyboard(user_id))
        return ConversationHandler.END

    error = validate_name(text)
    if error:
        await tg(update.message.reply_text, error + "\nПопробуй ещё:")
        return WAITING_FOR_NEW_NAME

    old_name = user_data[user_id]["name"]
    user_data[user_id]["name"] = text
    user_data[user_id].pop("awaiting_new_name", None)
    save_data()

    await tg(update.message.reply_text,
        f"✅ Имя успешно изменено!\n\n"
        f"Было: {old_name}\n"
        f"Стало: {text}\n\n"
        f"Теперь ты будешь отображаться в проверках как {text}",
        reply_markup=get_main_keyboard(user_id)
    )
    return ConversationHandler.END


SESSION_SKILL_RE = re.compile(r'^\s*#skill=(-?\d+(?:[.,]\d+)?)(?:\|[^\n]*)?\s*$', re.MULTILINE)
SUBMIT_RE = re.compile(r'^\s*#submit=\d+\s*$', re.MULTILINE)
YANG_RE = re.compile(r'^\s*#yang=(\S+)\s*$', re.MULTILINE)


def _fmt_skill_num(x):
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return str(x).replace('.', ',')


def _extract_skill_value(text):
    my = YANG_RE.search(text)
    batch_yang = my.group(1).strip() if my else None
    if batch_yang and not is_valid_url(batch_yang):
        batch_yang = None

    value = None
    for m in SESSION_SKILL_RE.finditer(text):
        try:
            v = float(m.group(1).replace(',', '.'))
        except ValueError:
            continue
        value = int(v) if v.is_integer() else v

    cleaned = YANG_RE.sub('', SUBMIT_RE.sub('', SESSION_SKILL_RE.sub('', text))).strip()
    return value, batch_yang, cleaned


def _ach_skill_snapshot(uid):
    """Снимок ach-состояния навыка ДО применения нового значения — нужен, чтобы
    отмена отправки могла откатить peak/skill_ups/comebacks/hi_days так же
    точно, как откатывает matches и votes, а не оставляла их зачисленными
    навсегда, пока пользователь не отправит что-то ещё."""
    rec = _ach_rec(uid)
    day = _ach_day_key(uid)
    return {
        "day": day,
        "skill": dict(rec.get("skill") or {}),
        "day_state": dict(rec.get("days", {}).get(day) or {}),
        "skill_ups": int((rec.get("counters") or {}).get("skill_ups", 0) or 0),
        "comebacks": int((rec.get("counters") or {}).get("comebacks", 0) or 0),
    }


def _ach_skill_restore(uid, snap):
    if not snap:
        return
    rec = _ach_rec(uid)
    rec["skill"] = dict(snap.get("skill") or {})
    day = snap.get("day")
    if day:
        prev_day_state = snap.get("day_state") or {}
        # день мог обзавестись другими полями (сессии/матчи) уже после снимка —
        # трогаем только skill-related ключи, остальное дня не откатываем.
        d = _ach_day(rec, day)
        for key in ("skill_last", "skill_n", "skill_min", "skill_max", "hi_counted"):
            if key in prev_day_state:
                d[key] = prev_day_state[key]
            else:
                d.pop(key, None)
    c = rec["counters"]
    c["skill_ups"] = max(0, int(snap.get("skill_ups", 0) or 0))
    c["comebacks"] = max(0, int(snap.get("comebacks", 0) or 0))
    save_achievements()


def _apply_skill_value(user_id, value):
    if value is None:
        return
    d = user_data.setdefault(user_id, {})
    d["session_skill"] = value
    d["session_skill_at"] = datetime.now().isoformat()
    save_data()
    ach_on_skill(user_id, value)


SKILL_PROMPT_INTERVAL_HOURS = 1


def _needs_skill_prompt(user_id) -> bool:
    at = user_data.get(user_id, {}).get("session_skill_at")
    if not at:
        return True
    try:
        dt = datetime.fromisoformat(at)
    except ValueError:
        return True
    return (datetime.now() - dt) >= timedelta(hours=SKILL_PROMPT_INTERVAL_HOURS)


def _parse_manual_skill_answer(text: str):
    t = (text or "").strip().replace(",", ".")
    try:
        val = float(t)
    except ValueError:
        return None
    if val != val or val < 0 or val > 100:
        return None
    if val.is_integer():
        val = int(val)
    return val


SKILL_PROMPT_TEXT = (
    "❓ Перед отправкой сессий: какой у тебя СЕЙЧАС навык в разметке поисковых сессий?\n"
    "Введи число от 0 до 100."
)


async def _handle_skill_gate(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    data = user_data.get(user_id)
    if not data or not data.get("awaiting_manual_skill"):
        return False

    stripped = (text or "").strip()
    if stripped in ALL_BUTTONS or stripped.startswith("/"):
        return False

    tagged, _batch_yang, _rest = _extract_skill_value(text)
    if tagged is not None:
        data["awaiting_manual_skill"] = False
        data.pop("pending_links_text", None)
        save_data()
        return False

    val = _parse_manual_skill_answer(text)
    if val is None:
        await tg(update.message.reply_text,
            "❌ Не понял. " + SKILL_PROMPT_TEXT)
        return True

    data["session_skill"] = val
    data["session_skill_at"] = datetime.now().isoformat()
    data["awaiting_manual_skill"] = False
    ach_on_skill(user_id, val)
    pending_text = data.pop("pending_links_text", None)
    save_data()

    await tg(update.message.reply_text,
        f"✅ Записал навык: {_fmt_skill_num(val)}. Спасибо!",
        reply_markup=get_main_keyboard(user_id))

    if pending_text:
        await _save_links_from_text(update, context, pending_text)
    await ach_award(context.bot, user_id)
    return True


def _migrate_submission_stack(d) -> bool:
    if not isinstance(d, dict):
        return False
    if isinstance(d.get("submissions"), list):
        return False
    old = d.get("last_submission")
    d["submissions"] = [old] if isinstance(old, dict) else []
    return True


def _push_submission(user_id, entry) -> None:
    d = user_data.setdefault(user_id, {})
    _migrate_submission_stack(d)
    stack = d["submissions"]
    stack.append(entry)
    del stack[:-SUBMISSION_STACK_LIMIT]
    d["last_submission"] = entry


def _peek_submission(user_id):
    d = user_data.get(user_id)
    if not isinstance(d, dict):
        return None
    _migrate_submission_stack(d)
    stack = d.get("submissions") or []
    return stack[-1] if stack else None


def _pop_submission(user_id):
    d = user_data.setdefault(user_id, {})
    _migrate_submission_stack(d)
    stack = d["submissions"]
    entry = stack.pop() if stack else None
    if stack:
        d["last_submission"] = stack[-1]
    else:
        d.pop("last_submission", None)
    return entry


def _capture_skill_snapshot(user_id: int) -> dict:
    d = user_data.get(user_id, {})
    return {
        "session_skill": d.get("session_skill"),
        "session_skill_at": d.get("session_skill_at"),
    }


def _restore_skill_snapshot(user_id: int, snap: dict) -> None:
    if not snap:
        return
    d = user_data.setdefault(user_id, {})
    for key in ("session_skill", "session_skill_at"):
        if snap.get(key) is None:
            d.pop(key, None)
        else:
            d[key] = snap[key]
    _ach_skill_restore(user_id, snap.get("ach"))


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tg(update.message.reply_text,
        f"Твой ID: <code>{update.effective_user.id}</code>\n\n"
        "Вставь его в «Параметры» расширения (правой кнопкой по иконке).",
        parse_mode="HTML")


def _merge_links_into_history(user_id, entries, current_time):
    hist = user_links_history.get(user_id)
    if not isinstance(hist, list):
        hist = []
        user_links_history[user_id] = hist
    hist[:] = [h for h in hist if isinstance(h, dict)]
    existing = {h.get("url"): h for h in hist}
    added_urls = []
    touched = []
    for i, (s, y) in enumerate(entries, start=1):
        rec = existing.get(s)
        if rec is None:
            rec = {"url": s, "row": i, "yang": y, "added_at": current_time}
            hist.append(rec)
            existing[s] = rec
            added_urls.append(s)
            continue
        touched.append({
            "url": s,
            "row": rec.get("row"),
            "yang": rec.get("yang"),
            "added_at": rec.get("added_at"),
        })
        rec["row"] = i
        rec["added_at"] = current_time
        if y:
            rec["yang"] = y
    return added_urls, touched


async def _save_links_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id

    raw_text = text
    skill_value, batch_yang, text = _extract_skill_value(text)

    if skill_value is None and _needs_skill_prompt(user_id):
        user_data[user_id]["awaiting_manual_skill"] = True
        user_data[user_id]["pending_links_text"] = raw_text
        save_data()
        await tg(update.message.reply_text, SKILL_PROMPT_TEXT)
        return False

    if len(text) > 10000:
        await tg(update.message.reply_text, "❌ Слишком длинное сообщение")
        return False

    if user_id in user_data and isinstance(user_data[user_id], dict):
        user_data[user_id]["username"] = update.effective_user.username

    all_entries = _parse_link_entries(text)
    if batch_yang:
        all_entries = [(s, y or batch_yang) for (s, y) in all_entries]
    valid_count = len(all_entries)
    entries = all_entries[:MAX_LINKS]

    if not entries:
        if skill_value is not None:
            skill_snapshot = _capture_skill_snapshot(user_id)
            skill_snapshot["ach"] = _ach_skill_snapshot(user_id)
            _apply_skill_value(user_id, skill_value)
            _push_submission(user_id, {
                "added_urls": [],
                "urls": [],
                "prev_links": user_data[user_id].get("links") or {},
                "skill_snapshot": skill_snapshot,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            save_data()
            await ach_award(context.bot, user_id)
            return True
        await tg(update.message.reply_text,
            "❌ Не найдено корректных ссылок. Ссылка должна начинаться с http:// или https://")
        return False
    if valid_count > MAX_LINKS:
        await tg(update.message.reply_text, f"⚠️ Найдено {valid_count} ссылок, сохранены первые {MAX_LINKS}.")

    current_links = user_data.get(user_id, {}).get("links") or {}
    current_url_set = {v.get("url") for v in current_links.values() if v.get("url")}
    new_url_set = {s for s, y in entries}
    if current_url_set and new_url_set == current_url_set:
        await tg(update.message.reply_text,
            "⚠️ Похоже, это повторная отправка — точно такой же набор сессий уже был "
            "отправлен последний раз.\n"
            "Ничего не сохранено, чтобы не задублировать проверку.\n\n"
            "Сессии уже в работе — жми «✅ Проверить совпадения», отправлять их снова не нужно.\n"
            "Если это ссылки с другого аккаунта или ты хотел отправить что-то другое — "
            "проверь и пришли ещё раз.",
            reply_markup=get_main_keyboard(user_id))
        return False

    skill_snapshot = None
    if skill_value is not None:
        skill_snapshot = _capture_skill_snapshot(user_id)
        skill_snapshot["ach"] = _ach_skill_snapshot(user_id)
        _apply_skill_value(user_id, skill_value)

    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prev_links = user_data[user_id].get("links") or {}
        user_data[user_id]["links"] = {
            i: {"url": s, "yang": y, "added_at": current_time}
            for i, (s, y) in enumerate(entries, start=1)
        }
        user_data[user_id]["updated_at"] = datetime.now().isoformat()
        global user_links_history
        added_urls, touched = _merge_links_into_history(user_id, entries, current_time)
        _push_submission(user_id, {
            "added_urls": added_urls,
            "touched": touched,
            "urls": [s for s, _ in entries],
            "prev_links": prev_links,
            "skill_snapshot": skill_snapshot,
            "at": current_time,
        })
        save_data()
        save_user_links_history(user_links_history)
        ach_on_links(user_id, len(added_urls), len(entries), current_time)

        response = f"✅ Сохранено {len(entries)} ссылок:\n\n"
        shown_time = _fmt_local(current_time, user_id) or current_time
        for i, (s, y) in enumerate(entries, start=1):
            response += f"Ряд {i}: {s}\n   🕐 Добавлено: {shown_time}\n"
            if y:
                response += f"   🔗 Янг (задание {i}): {y}\n"
        if len(entries) < MAX_LINKS:
            response += "\n💡 Ты можешь отправить новые ссылки — они заменят существующие."
        if _auto_check_on(user_id):
            response += "\n\n🔍 Запускаю проверку совпадений — результат следующим сообщением."
        else:
            response += "\n\n🐢 Автопроверка выключена — жми «✅ Проверить совпадения»."
    except Exception as e:
        logger.error(f"Ошибка сохранения ссылок: {e}")
        try:
            await tg(update.message.reply_text, "❌ Ошибка при сохранении ссылок")
        except Exception:
            pass
        return False

    try:
        await tg(update.message.reply_text, response, reply_markup=get_main_keyboard(user_id))
    except Exception as e:
        logger.warning(f"Не удалось отправить подтверждение сохранения ({user_id}): {e}")

    if AUTO_CHECK_ON_SAVE and _auto_check_on(user_id):
        try:
            await _check_matches_impl(update, context, user_id, auto=True)
        except Exception as e:
            logger.warning(f"Автопроверка после сохранения не сработала ({user_id}): {e}")
            try:
                await tg(update.message.reply_text,
                    "⚠️ Ссылки сохранены, но автопроверка не прошла (связь).\n"
                    "Нажми «✅ Проверить совпадения» — ничего не потеряется.")
            except Exception:
                pass
    await ach_award(context.bot, user_id)
    return True


async def process_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        await tg(update.message.reply_text, "❌ Сообщение пустое. Отправь ссылки:")
        return WAITING_FOR_LINKS
    if not is_user_active(user_id):
        await tg(update.message.reply_text, "❌ Ты ещё не зарегистрирован!", reply_markup=get_start_keyboard())
        return ConversationHandler.END
    if await _handle_skill_gate(update, context, text):
        return WAITING_FOR_LINKS
    if text == "✏️ Сменить имя":
        return await change_name_start(update, context)
    if text in MENU_BUTTONS:
        await handle_main_menu(update, context)
        return WAITING_FOR_LINKS
    if user_data.get(user_id, {}).get("awaiting_new_name"):
        return await change_name_save(update, context)
    await _save_links_from_text(update, context, text)
    return WAITING_FOR_LINKS


async def show_my_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text, "❌ Ты ещё не зарегистрирован!", reply_markup=get_start_keyboard())
        return
    try:
        name = user_data[user_id]["name"]
        links = user_data[user_id].get("links", {})
        if not links:
            await tg(update.message.reply_text, f"📋 У тебя ({name}) пока нет ссылок.")
            return
        response = f"📋 Твои ссылки ({name}):\n\n"
        for row in range(1, MAX_LINKS + 1):
            if row in links:
                link_data = links[row]
                if isinstance(link_data, dict):
                    url = link_data.get("url", link_data)
                    added_at = _fmt_local(link_data.get("added_at", ""), user_id) or "неизвестно"
                    yang = link_data.get("yang")
                else:
                    url = link_data
                    added_at = "старая версия (без времени)"
                    yang = None
                response += f"Ряд {row}: {url}\n"
                response += f"   🕐 Добавлено: {added_at}\n"
                if yang:
                    response += f"   🔗 Янг (задание {row}): {yang}\n"
            else:
                response += f"Ряд {row}: ⏳ пусто\n"
        await tg(update.message.reply_text, response)
    except Exception as e:
        logger.error(f"Ошибка в show_my_links: {e}")
        await tg(update.message.reply_text, "❌ Ошибка при отображении ссылок")


async def show_active_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text, "❌ Ты ещё не зарегистрирован!", reply_markup=get_start_keyboard())
        return
    try:
        people = []
        for uid, data in user_data.items():
            if not isinstance(data, dict) or not data.get("registered"):
                continue
            if not is_approved(uid):
                continue
            name = data.get("name")
            if not name or name in ALL_BUTTONS:
                continue
            links = data.get("links", {})
            if not links:
                continue
            sessions = []
            for row in sorted(links.keys()):
                ld = links[row]
                if isinstance(ld, dict):
                    url = ld.get("url")
                    added_at = ld.get("added_at", "")
                else:
                    url = ld
                    added_at = ""
                if url:
                    sessions.append((url, added_at))
            if not sessions:
                continue
            newest = max((a for _, a in sessions), default="")
            sort_dt = _parse_added_at(newest) or datetime.min
            people.append((sort_dt, newest, name, [u for u, _ in sessions], uid))

        if not people:
            best_uid, best_dt = None, None
            for uid, hist in user_links_history.items():
                for e in hist:
                    dt = _parse_added_at(e.get("added_at", ""))
                    if dt and (best_dt is None or dt > best_dt):
                        best_dt, best_uid = dt, uid
            urls, when, name = [], "", ""
            if best_uid is not None:
                data = user_data.get(best_uid, {})
                name = data.get("name", "Участник")
                hist = user_links_history.get(best_uid, [])
                target_ts = next(
                    (e.get("added_at") for e in hist if _parse_added_at(e.get("added_at", "")) == best_dt),
                    None
                )
                batch = sorted(
                    (e for e in hist if e.get("added_at") == target_ts),
                    key=lambda e: e.get("row", 0)
                )
                urls = [e.get("url") for e in batch if e.get("url")]
                when = _fmt_local(target_ts or "", user_id) or ""
            if not urls:
                await tg(update.message.reply_text,
                    "🔥 Сейчас ни у кого нет активных сессий.\n"
                    "Как только участники отправят ссылки — они появятся здесь."
                )
                return
            lines = [
                f"🔥 Последние актуальные сессии ({when or 'время неизвестно'}) "
                f"на данный момент от {html.escape(str(name))} (из истории, после рестарта бота):",
                ""
            ]
            for i, url in enumerate(urls, start=1):
                lines.append(f"{i}. {html.escape(url)}")
                lines.append("")
            block = "\n".join(lines).rstrip() + "\n\n"
            await send_blocks(context.bot, update.effective_chat.id, [block])
            return

        people.sort(key=lambda p: p[0], reverse=True)
        _, newest_at, name, urls, owner_uid = people[0]

        when = _fmt_local(newest_at, user_id) if newest_at else "время неизвестно"
        lines = [f"🔥 Последние актуальные сессии ({when}) на данный момент от {html.escape(str(name))}:", ""]
        for i, url in enumerate(urls, start=1):
            lines.append(f"{i}. {html.escape(url)}")
            lines.append("")
        block = "\n".join(lines).rstrip() + "\n\n"

        await send_blocks(context.bot, update.effective_chat.id, [block])
    except Exception as e:
        logger.error(f"Ошибка в show_active_sessions: {e}")
        await tg(update.message.reply_text, "❌ Ошибка при отображении актуальных сессий")


PARTICIPANT_HISTORY_LIMIT = 5


async def show_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        participants = []
        for user_id, data in user_data.items():
            if data.get("registered"):
                name = data.get("name")
                if not name or name in ALL_BUTTONS:
                    continue
                links = data.get("links", {})
                links_count = len(links)
                skill = data.get("session_skill")
                dt = _last_submission_dt(user_id, data)
                viewer_uid = update.effective_user.id
                when = _fmt_local_dt(dt, viewer_uid) if dt else "ещё не отправлял"
                parts = [f"{name} ({links_count}/{MAX_LINKS})"]
                if skill is not None:
                    parts.append(f"Навык: {_fmt_skill_num(skill)}")
                parts.append(f"🕐 {when}")
                line = " — ".join(parts)
                participants.append((user_id, name, line, dt))
        participants.sort(key=lambda p: p[3] or datetime.min, reverse=True)
        if not participants:
            await tg(update.message.reply_text, "👥 Пока нет участников. Пригласи друзей нажать /start!")
            return
        response = "👥 УЧАСТНИКИ СЕССИИ:\n\n" + "\n".join(p[2] for p in participants)
        response += f"\n\n📊 Всего: {len(participants)} человек"
        response += "\n\nЖми на имя ниже — покажу последние сессии участника."

        rows = []
        row = []
        for uid, name, _, _ in participants:
            row.append(InlineKeyboardButton(str(name)[:30], callback_data=f"ph:{uid}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        markup = InlineKeyboardMarkup(rows) if rows else None

        await tg(update.message.reply_text, response, reply_markup=markup)
    except Exception as e:
        logger.error(f"Ошибка в show_participants: {e}")
        await tg(update.message.reply_text, "❌ Ошибка при отображении участников")


async def participant_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await tg_answer(query, )
    try:
        viewer_uid = update.effective_user.id
        uid_str = query.data.split(":", 1)[1]
        uid = int(uid_str)
        data = user_data.get(uid, {})
        name = data.get("name", "Участник")
        hist = user_links_history.get(uid, [])

        entries = sorted(
            hist,
            key=lambda e: _parse_added_at(e.get("added_at", "")) or datetime.min,
            reverse=True,
        )[:PARTICIPANT_HISTORY_LIMIT]

        if not entries:
            await tg(query.message.reply_text, f"📭 У {html.escape(str(name))} пока нет сохранённых сессий.")
            return

        lines = [f"🕓 Последние сессии — {html.escape(str(name))}:", ""]
        for i, e in enumerate(entries, start=1):
            url = e.get("url", "?")
            added_at = _fmt_local(e.get("added_at", ""), viewer_uid) or "неизвестно"
            lines.append(f"{i}. {html.escape(str(url))}")
            lines.append(f"   🕐 {added_at}")
            lines.append("")
        block = "\n".join(lines).rstrip()

        await tg(query.message.reply_text, block, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Ошибка в participant_history_callback: {e}")
        await tg(query.message.reply_text, "❌ Ошибка при отображении истории участника")


async def cleanup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await tg(update.message.reply_text, "⛔ У вас нет прав на эту команду.")
        return
    try:
        st = cleanup_old_links()
        await tg(update.message.reply_text,
            "🧽 Очистка старого выполнена!\n\n"
            f"🔗 Ссылок удалено: {st['links']}\n"
            f"🏷 Вердиктов урлов: {st['verdicts']}\n"
            f"🗳 Голосований: {st['votes']}\n"
            f"📌 Пометок совпадений: {st['reported']}\n\n"
            f"📅 Удалялось старше: {st['cutoff']} (TTL {LINKS_TTL_DAYS} дн.)"
        )
    except Exception as e:
        logger.error(f"Ошибка ручной очистки: {e}")
        await tg(update.message.reply_text, "❌ Ошибка при очистке старого")


BUTTON_NAMES = ALL_BUTTONS


def _match_sig(users) -> list:
    return sorted(f"{u['uid']}@{u.get('added_at', '')}" for u in users)


def _sig_for_uids(link, uids) -> list:
    sig = []
    for uid in uids:
        added = ""
        for e in (user_links_history.get(uid) or []):
            if not isinstance(e, dict):
                continue
            if e.get("url") == link:
                added = (e or {}).get("added_at", "")
                break
        sig.append(f"{uid}@{added}")
    return sorted(sig)


def _reported_sig(link) -> set:
    prev = reported_matches.get(link)
    if not prev:
        return set()
    return {x if isinstance(x, str) else f"{x}@" for x in prev}


def _migrate_reported_matches() -> int:
    changed = 0
    for link, prev in list(reported_matches.items()):
        if not isinstance(prev, list) or all(isinstance(x, str) for x in prev):
            continue
        reported_matches[link] = _sig_for_uids(link, prev)
        changed += 1
    if changed:
        save_reported_matches(reported_matches)
        logger.info(f"Миграция reported_matches: обновлено записей {changed}.")
    return changed


def compute_matches():
    all_users = {}
    for uid, data in user_data.items():
        if not isinstance(data, dict) or not data.get("registered"):
            continue
        if not is_approved(uid):
            continue
        name = data.get("name")
        if not name or not isinstance(name, str) or name in BUTTON_NAMES:
            continue
        hist_links = user_links_history.get(uid) or []
        if not isinstance(hist_links, list):
            logger.warning(f"История ссылок {uid} повреждена, пропускаю")
            continue
        hist_links = [e for e in hist_links if isinstance(e, dict)]
        if hist_links:
            all_users[uid] = {"name": name, "username": data.get("username"),
                              "links": hist_links}

    all_links_map = {}
    for uid, data in all_users.items():
        for entry in data["links"]:
            link = entry.get("url")
            if not link or not isinstance(link, str):
                continue
            added_at = entry.get("added_at", "неизвестно")
            row = entry.get("row", "?")
            yang = entry.get("yang")
            bucket = all_links_map.setdefault(link, {})
            if uid not in bucket:
                bucket[uid] = {"name": data["name"], "row": row, "uid": uid,
                               "username": data.get("username"),
                               "added_at": added_at, "yang": yang}

    matches = []
    users_with_matches = set()
    for link, users_dict in all_links_map.items():
        if len(users_dict) >= 2:
            users = list(users_dict.values())
            users.sort(key=lambda u: u.get("added_at", ""), reverse=True)
            matches.append({"link": link, "users": users})
            for u in users:
                users_with_matches.add(u["uid"])

    matches.sort(key=lambda m: max((u.get("added_at", "") for u in m["users"]), default=""), reverse=True)
    return all_users, matches, users_with_matches


def mention_href(uid, username=None):
    if username:
        safe = re.sub(r'[^A-Za-z0-9_]', '', str(username))
        if safe:
            return f"https://t.me/{safe}"
    return f"tg://user?id={int(uid) if str(uid).lstrip('-').isdigit() else 0}"


def format_match_block(idx, match, viewer_uid=None):
    block = f"{num_emoji(idx)} Сессия: {html.escape(str(match['link']))}\n"
    block += "\n"
    for u in match["users"]:
        name = html.escape(str(u['name']))
        name = f'<a href="{mention_href(u.get("uid"), u.get("username"))}">{name}</a>'
        skill = (user_data.get(u.get("uid"), {}) or {}).get("session_skill")
        added_at = _fmt_local(u.get("added_at", ""), viewer_uid) or u.get("added_at", "")
        block += f"   → {name} (ряд {u['row']})\n"
        meta = f"      🕐 {added_at}"
        if skill is not None:
            meta += f" • Навык: {_fmt_skill_num(skill)}"
        block += meta + "\n"
        yang = u.get("yang")
        if yang:
            block += f"      🔗 Янг (задание {u['row']}): {html.escape(str(yang))}\n"
        block += "\n"
    return block


def format_status_block(all_users, display_uids, mention: bool = False):
    display = [uid for uid in all_users if uid in display_uids]
    if not display:
        return ""
    s = "👥 СТАТУС УЧАСТНИКОВ:\n"
    lines = []
    for uid in display:
        name = html.escape(str(all_users[uid]["name"]))
        if mention:
            href = mention_href(uid, all_users[uid].get("username"))
            name = f'<a href="{href}">{name}</a>'
        lines.append(f"• {name} → ✅ есть совпадения")
    s += "\n".join(lines)
    return s


NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def num_emoji(idx):
    return NUM_EMOJI[idx - 1] if 1 <= idx <= len(NUM_EMOJI) else f"{idx}️⃣"


def _vote_label(code):
    return "🔴 Робот 🤖" if code == "r" else "🟢 Человек 🧑"


def _vote_label_final(code):
    circle = "🔴" if code == "r" else "🟢"
    name = "Робот 🤖" if code == "r" else "Человек 🧑"
    return f"🏁{circle} финальный {name}"


def pair_partner_present(voter_uid, member_uids):
    members = set(member_uids)
    for a, b in VOTE_PAIRS:
        if voter_uid == a and b in members:
            return b
        if voter_uid == b and a in members:
            return a
    return None


def cross_voters_for(member_uids):
    members = set(member_uids)
    extra = set()
    for a, b in VOTE_PAIRS:
        if a in members:
            extra.add(b)
        if b in members:
            extra.add(a)
    return extra - members


def next_vote_token():
    global _vote_seq
    _vote_seq += 1
    return str(_vote_seq)


def vote_keyboard(token, phase="initial", viewer_uid=None):
    is_admin_viewer = viewer_uid in ADMIN_IDS
    if phase == "final":
        rows = [
            [InlineKeyboardButton("Финал — Робот 🤖", callback_data=f"mv:{token}:fr"),
             InlineKeyboardButton("Финал — Человек 🧑", callback_data=f"mv:{token}:fh")],
            [InlineKeyboardButton("❌ Отменить мою оценку", callback_data=f"mv:{token}:cv")],
            [InlineKeyboardButton("🔔 Напомнить участнику", callback_data=f"mv:{token}:rm")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("Робот 🤖", callback_data=f"mv:{token}:ir"),
             InlineKeyboardButton("Человек 🧑", callback_data=f"mv:{token}:ih")],
            [InlineKeyboardButton("⏩ Сразу финал: 🤖", callback_data=f"mv:{token}:fr"),
             InlineKeyboardButton("⏩ Сразу финал: 🧑", callback_data=f"mv:{token}:fh")],
            [InlineKeyboardButton("❌ Отменить мою оценку", callback_data=f"mv:{token}:cv")],
            [InlineKeyboardButton("🔔 Напомнить участнику", callback_data=f"mv:{token}:rm")],
        ]
    if is_admin_viewer:
        rows.append([InlineKeyboardButton("🛠 За участника (админ)", callback_data=f"mv:{token}:adm")])
    return InlineKeyboardMarkup(rows)


def proxy_pick_keyboard(token, users):
    rows = []
    for u in users:
        uid = u["uid"]
        name = str(u["name"])[:20]
        rows.append([
            InlineKeyboardButton(f"{name}: 🤖", callback_data=f"mva:{token}:{uid}:ir"),
            InlineKeyboardButton("🧑", callback_data=f"mva:{token}:{uid}:ih"),
            InlineKeyboardButton("фин 🤖", callback_data=f"mva:{token}:{uid}:fr"),
            InlineKeyboardButton("фин 🧑", callback_data=f"mva:{token}:{uid}:fh"),
        ])
    return InlineKeyboardMarkup(rows)


def render_vote_text(state, viewer_uid=None):
    text = ""
    if state.get("prefix"):
        text += state["prefix"] + "\n"
    text += f"{num_emoji(state['idx'])} Сессия: {html.escape(str(state['link']))}\n"
    carried = state.get("carried")
    if carried and (carried.get("initial") or carried.get("final")):
        shown = carried.get("final") or carried.get("initial")
        by = html.escape(str(carried.get("by", "")))
        text += "\n"
        text += f"Оценка этого урла: {_vote_label(shown)}"
        if by:
            text += f" (поставил: {by})"
        text += "\n"
    text += "\n"
    for u in state["users"]:
        uid = str(u["uid"])
        name = html.escape(str(u['name']))
        name = f'<a href="{mention_href(u.get("uid"), u.get("username"))}">{name}</a>'
        skill = (user_data.get(u.get("uid"), {}) or {}).get("session_skill")
        added_at = _fmt_local(u.get("added_at", ""), viewer_uid) or u.get("added_at", "")
        text += f"   → {name} (ряд {u['row']})\n"
        meta = f"      🕐 {added_at}"
        if skill is not None:
            meta += f" • Навык: {_fmt_skill_num(skill)}"
        text += meta + "\n"
        init = state["ratings"].get(uid)
        fin = state["final_ratings"].get(uid)
        if init:
            line = f"      Оценка: {_vote_label(init)}"
            if fin:
                line += f" → {_vote_label_final(fin)}"
            text += line + "\n"
        elif fin:
            text += f"      {_vote_label_final(fin)}\n"
        yang = u.get("yang")
        if yang:
            text += f"      🔗 Янг (задание {u['row']}): {html.escape(str(yang))}\n"
        text += "\n"
    if state["phase"] == "initial":
        text += "🗳 Оценка (можно сразу финальную, если уверены) — только участники:"
    else:
        text += "⚠️ Финальная (окончательная) оценка — только участники совпадения:"
    if state.get("suffix"):
        text += "\n\n" + state["suffix"]
    return text


def _record_vote(state, target_uid, val, is_final, voter_name):
    if is_final:
        state["final_ratings"][str(target_uid)] = val
    else:
        state["ratings"][str(target_uid)] = val
    global link_verdicts
    link = state["link"]
    v = link_verdicts.setdefault(link, {"initial": None, "final": None, "by": "", "at": ""})
    if is_final:
        v["final"] = val
    else:
        v["initial"] = val
    v["by"] = voter_name
    v["at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    users_map = v.setdefault("users", {})
    urec = users_map.setdefault(str(target_uid), {"initial": None, "final": None})
    if is_final:
        urec["final"] = val
    else:
        urec["initial"] = val
    urec["by"] = voter_name
    urec["at"] = v["at"]
    state["carried"] = {"initial": v["initial"], "final": v["final"], "by": v["by"]}
    save_link_verdicts()
    save_match_votes()


def _cancel_vote(state, target_uid) -> bool:
    key = str(target_uid)
    had_init = state["ratings"].pop(key, None) is not None
    had_final = state["final_ratings"].pop(key, None) is not None
    if not (had_init or had_final):
        return False
    global link_verdicts
    link = state["link"]
    v = link_verdicts.get(link)
    if isinstance(v, dict):
        users_map = v.get("users")
        if isinstance(users_map, dict):
            users_map.pop(key, None)
        latest = None
        if isinstance(users_map, dict):
            for urec in users_map.values():
                if not isinstance(urec, dict):
                    continue
                if not (urec.get("initial") or urec.get("final")):
                    continue
                if latest is None or (urec.get("at") or "") >= (latest.get("at") or ""):
                    latest = urec
        if latest is not None:
            v["initial"] = latest.get("initial")
            v["final"] = latest.get("final")
            v["by"] = latest.get("by", "")
            v["at"] = latest.get("at", "")
        else:
            v["initial"] = None
            v["final"] = None
            v["by"] = ""
            v["at"] = ""
        state["carried"] = {"initial": v["initial"], "final": v["final"], "by": v["by"]}
    else:
        state.pop("carried", None)
    save_link_verdicts()
    save_match_votes()
    return True


def carried_ratings_for(link, member_uids):
    v = link_verdicts.get(link)
    if not v:
        return {}, {}
    per_user = v.get("users") if isinstance(v.get("users"), dict) else {}
    if not isinstance(per_user, dict):
        per_user = {}
    ratings, final_ratings = {}, {}
    for uid in member_uids:
        key = str(uid)
        urec = per_user.get(key)
        if not isinstance(urec, dict):
            continue
        if urec.get("initial"):
            ratings[key] = urec["initial"]
        if urec.get("final"):
            final_ratings[key] = urec["final"]
    return ratings, final_ratings


def _match_voted_uids(state) -> set:
    return {int(k) for k in state["ratings"].keys()} | {int(k) for k in state["final_ratings"].keys()}


def _match_pending_uids(state) -> list:
    member_uids = [u["uid"] for u in state["users"]]
    voted = _match_voted_uids(state)
    return [uid for uid in member_uids if uid not in voted]


def match_recipient_uids(state):
    member_uids = [u["uid"] for u in state["users"]]
    uids = list(dict.fromkeys(member_uids))
    for extra in cross_voters_for(member_uids):
        if extra not in uids:
            uids.append(extra)
    return uids


async def refresh_match_message(bot, state):
    copies = state.get("copies")
    if not copies:
        if state.get("chat_id") and state.get("message_id"):
            copies = [{"chat_id": state["chat_id"], "message_id": state["message_id"]}]
        else:
            return
    token = state.get("token", "")
    for copy in copies:
        viewer_uid = copy.get("chat_id")
        text = render_vote_text(state, viewer_uid=viewer_uid)
        kb = vote_keyboard(token, state["phase"], viewer_uid=viewer_uid)
        try:
            await tg(bot.edit_message_text,
                chat_id=copy["chat_id"], message_id=copy["message_id"],
                text=text, parse_mode="HTML", reply_markup=kb,
                disable_web_page_preview=True)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                logger.warning(f"Не удалось обновить копию совпадения {copy}: {e}")
        except Exception as e:
            logger.warning(f"Не удалось обновить копию совпадения {copy}: {e}")


async def delete_match_copies(bot, state):
    copies = state.get("copies")
    if not copies and state.get("chat_id") and state.get("message_id"):
        copies = [{"chat_id": state["chat_id"], "message_id": state["message_id"]}]
    n = 0
    for copy in (copies or []):
        try:
            await tg(bot.delete_message, chat_id=copy["chat_id"],
                                     message_id=copy["message_id"])
            n += 1
        except Exception:
            pass
    return n


async def _clear_reminder_msg(bot, state, target_uid):
    reminder_msgs = state.get("reminder_msgs") or {}
    msg_id = reminder_msgs.pop(str(target_uid), None)
    if not msg_id:
        return
    try:
        await tg(bot.delete_message, chat_id=target_uid, message_id=msg_id)
    except Exception:
        pass
    save_match_votes()


async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        _, token, action = q.data.split(":")
    except Exception:
        await tg_answer(q, )
        return
    state = match_votes.get(token)
    if not state:
        await tg_answer(q, "Эта оценка больше не активна.", show_alert=True)
        return
    state["token"] = token
    uid = q.from_user.id
    if action == "adm":
        if uid not in ADMIN_IDS:
            await tg_answer(q, "⛔ Голосовать за участников может только администратор.", show_alert=True)
            return
        try:
            await tg(context.bot.send_message,
                chat_id=uid,
                text=(f"🛠 Голос за участника — совпадение {num_emoji(state['idx'])}:\n"
                      f"{html.escape(str(state['link']))}\n\nВыбери участника и оценку:"),
                parse_mode="HTML",
                reply_markup=proxy_pick_keyboard(token, state["users"]))
            await tg_answer(q, "Открыл выбор в личке с ботом ✅", show_alert=True)
        except Exception as e:
            logger.error(f"Не удалось открыть прокси-голос: {e}")
            await tg_answer(q, "Не удалось открыть в личке. Напиши боту /start и попробуй снова.", show_alert=True)
        return
    member_uids = [u["uid"] for u in state["users"]]
    effective_allowed = set(state["allowed_uids"]) | cross_voters_for(member_uids)
    if uid not in effective_allowed:
        await tg_answer(q, "⛔ Голосовать могут только участники этого совпадения.", show_alert=True)
        return
    if action == "rm":
        pending = [t for t in _match_pending_uids(state) if t != uid]
        if not pending:
            await tg_answer(q, "Все участники уже проголосовали ✅", show_alert=True)
            return
        remind_name = (user_data.get(uid, {}) or {}).get("name") or \
            next((u["name"] for u in state["users"] if u["uid"] == uid), "Участник")
        now = datetime.now()
        last_remind = state.setdefault("last_remind", {})
        sent_names, still_waiting = [], False
        for target_uid in pending:
            key = str(target_uid)
            prev = last_remind.get(key)
            if prev:
                try:
                    if (now - datetime.strptime(prev, "%Y-%m-%d %H:%M:%S")).total_seconds() < REMIND_COOLDOWN_SECONDS:
                        still_waiting = True
                        continue
                except Exception:
                    pass
            text = (f"🔔 <b>{html.escape(str(remind_name))}</b> ждёт от тебя оценки "
                    f"в совпадении {num_emoji(state['idx'])}:\n"
                    f"{html.escape(str(state['link']))}")
            try:
                reminder_msgs = state.setdefault("reminder_msgs", {})
                old_reminder_id = reminder_msgs.get(key)
                if old_reminder_id:
                    try:
                        await tg(context.bot.delete_message, chat_id=target_uid, message_id=old_reminder_id)
                    except Exception:
                        pass
                msg = await tg(context.bot.send_message,
                    chat_id=target_uid, text=text, parse_mode="HTML",
                    reply_markup=vote_keyboard(token, state["phase"], viewer_uid=target_uid),
                    disable_web_page_preview=True)
                reminder_msgs[key] = msg.message_id
                last_remind[key] = now.strftime("%Y-%m-%d %H:%M:%S")
                target_name = next((u["name"] for u in state["users"] if u["uid"] == target_uid), str(target_uid))
                sent_names.append(target_name)
            except Exception as e:
                logger.warning(f"Не удалось отправить напоминание {target_uid}: {e}")
        save_match_votes()
        if sent_names:
            await tg_answer(q, f"Напоминание отправлено: {', '.join(sent_names)} 🔔", show_alert=True)
        elif still_waiting:
            await tg_answer(q, "Уже напоминали недавно, подожди немного ⏳", show_alert=True)
        else:
            await tg_answer(q, "Не удалось отправить напоминание.", show_alert=True)
        return
    if action == "cv":
        target_uid = uid
        if uid not in member_uids:
            partner = pair_partner_present(uid, member_uids)
            if partner is not None:
                target_uid = partner
        had = _cancel_vote(state, target_uid)
        if not had:
            await tg_answer(q, "У тебя пока нет оценки здесь — нечего отменять.", show_alert=True)
            return
        await _clear_reminder_msg(context.bot, state, target_uid)
        await refresh_match_message(context.bot, state)
        await tg_answer(q, "Оценка отменена ✅", show_alert=True)
        return
    if action in ("r", "h"):
        val = action
        is_final = (state["phase"] == "final")
    elif action in ("ir", "ih", "fr", "fh"):
        val = action[1]
        is_final = action[0] == "f"
    else:
        await tg_answer(q, )
        return
    target_uid = uid
    if uid not in member_uids:
        partner = pair_partner_present(uid, member_uids)
        if partner is not None:
            target_uid = partner
    voter = ""
    if target_uid in user_data and user_data[target_uid].get("name"):
        voter = user_data[target_uid]["name"]
    _record_vote(state, target_uid, val, is_final, voter)
    if ach_vote_counted(state, target_uid, is_final):
        credit = ach_on_vote(target_uid, is_final, state.get("created_at"))
        phase_key = "final" if is_final else "init"
        vc = state.setdefault("vote_credit", {}).setdefault(phase_key, {})
        vc[str(target_uid)] = credit
        save_match_votes()
    await _clear_reminder_msg(context.bot, state, target_uid)
    note = "Финальная оценка учтена ✅" if is_final else "Первичная оценка учтена ✅"
    await refresh_match_message(context.bot, state)
    await tg_answer(q, note)
    await ach_award(context.bot, uid)


async def proxy_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if uid not in ADMIN_IDS:
        await tg_answer(q, "⛔ Только администратор.", show_alert=True)
        return
    try:
        _, token, target_str, action = q.data.split(":")
        target_uid = int(target_str)
    except Exception:
        await tg_answer(q, )
        return
    state = match_votes.get(token)
    if not state:
        await tg_answer(q, "Это совпадение больше не активно.", show_alert=True)
        return
    if target_uid not in state["allowed_uids"]:
        await tg_answer(q, "Этот участник не относится к совпадению.", show_alert=True)
        return
    if action in ("ir", "ih", "fr", "fh"):
        val = action[1]
        is_final = action[0] == "f"
    else:
        await tg_answer(q, )
        return
    target_name = ""
    if target_uid in user_data and user_data[target_uid].get("name"):
        target_name = user_data[target_uid]["name"]
    _record_vote(state, target_uid, val, is_final, target_name)
    if ach_vote_counted(state, target_uid, is_final):
        credit = ach_on_vote(target_uid, is_final, state.get("created_at"))
        phase_key = "final" if is_final else "init"
        vc = state.setdefault("vote_credit", {}).setdefault(phase_key, {})
        vc[str(target_uid)] = credit
    state.setdefault("token", token)
    await _clear_reminder_msg(context.bot, state, target_uid)
    await refresh_match_message(context.bot, state)
    kind = "финальная" if is_final else "первичная"
    await tg_answer(q, f"{target_name or 'участник'}: {kind} {_vote_label(val)} ✅", show_alert=True)
    await ach_award(context.bot, target_uid)


def match_member_keys(state) -> set:
    return {str(u["uid"]) for u in (state.get("users") or [])}


async def send_personal_match_copies(context, new_matches, total_matches, initiator_name, check_time, initiator_id):
    for uid in PERSONAL_NOTIFY_IDS:
        own = [m for m in new_matches
               if any(u["uid"] == uid for u in m["users"])]
        try:
            if own:
                blocks = [
                    f"🔔 ТВОИ НОВЫЕ СОВПАДЕНИЯ 🔔\n"
                    f"👤 Запустил проверку: {html.escape(str(initiator_name))}\n"
                    f"🕐 {check_time}\n\n"
                    f"🆕 С твоим участием: {len(own)} (всего по чату: {total_matches})\n\n"
                ]
                for idx, m in enumerate(own, start=1):
                    blocks.append(format_match_block(idx, m, viewer_uid=uid))
                await send_blocks(context.bot, uid, blocks)
            elif uid == initiator_id:
                await tg(context.bot.send_message,
                    chat_id=uid,
                    text=(
                        f"🔔 РЕЗУЛЬТАТ ПРОВЕРКИ (лично тебе) 🔔\n"
                        f"👤 Запустил проверку: {initiator_name}\n"
                        f"🕐 {check_time}\n\n"
                        f"✅ Новых совпадений с твоим участием нет.\n"
                        f"📊 Всего совпадений в общем пуле: {total_matches}"
                    ),
                )
        except Exception as e:
            logger.warning(f"Не удалось отправить личную копию совпадений {uid}: {e}")


async def check_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await _check_matches_impl(update, context, user_id)


async def _check_matches_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int,
                              auto: bool = False):
    async def _say(text):
        try:
            await tg(update.message.reply_text, text)
        except Exception as e:
            logger.warning(f"Не удалось ответить о проверке {user_id}: {e}")

    if user_id in user_data and user_data[user_id].get("registered"):
        initiator_name = user_data[user_id]["name"]
    else:
        initiator_name = "Неизвестный"

    current_links = user_data.get(user_id, {}).get("links") or {}
    if not current_links:
        if auto:
            return
        await _say(
            "⚠️ У тебя нет ссылок в текущей сессии (0/5).\n"
            "Сначала отправь свои ссылки, потом запускай проверку."
        )
        return

    now_time = datetime.now().timestamp()
    if not auto and user_id in last_check_time:
        if now_time - last_check_time[user_id] < CHECK_COOLDOWN:
            await tg(update.message.reply_text, f"⏳ Подожди {CHECK_COOLDOWN} секунд между проверками")
            return

    try:
        all_users, matches, _users_with_matches = compute_matches()
        if len(all_users) < 2:
            head = "🔍 Проверил сразу после сохранения.\n" if auto else ""
            await _say(head + "⚠️ Недостаточно участников! Нужно минимум 2 человека.")
            return
        if user_id not in all_users:
            head = "🔍 Проверил сразу после сохранения.\n" if auto else ""
            await _say(head + "⚠️ У тебя нет добавленных ссылок! Сначала добавь ссылки.")
            return
        if not auto:
            last_check_time[user_id] = now_time

        global reported_matches
        new_matches = []
        for m in matches:
            if user_id not in {u["uid"] for u in m["users"]}:
                continue
            link = m["link"]
            cur_sig = set(_match_sig(m["users"]))
            prev_sig = _reported_sig(link)
            if not prev_sig or (cur_sig - prev_sig):
                m["_prev_sig"] = prev_sig
                new_matches.append(m)

        current_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for m in new_matches:
            names = [u.get("name") or str(u["uid"]) for u in m["users"]]
            logger.info(f"MATCH: {' + '.join(names)} — совпало ({current_check_time})")

        own_new = [m for m in new_matches if user_id in {u["uid"] for u in m["users"]}]
        foreign_new = len(new_matches) - len(own_new)

        def _summary_text(unreachable_note=""):
            if own_new:
                s = (f"🆕 Твоих новых совпадений: {len(own_new)}\n"
                     f"📊 Всего совпадений в общем пуле за {LINKS_TTL_DAYS} дн.: {len(matches)}")
            else:
                s = (f"✅ Новых совпадений с твоим участием нет — "
                     f"твои ссылки проверены, ни с кем не пересеклись.\n"
                     f"📊 Всего совпадений в общем пуле за {LINKS_TTL_DAYS} дн.: {len(matches)}")
            if foreign_new:
                s += (f"\n📨 Ещё {foreign_new} чужих новых совпадений разослано "
                      f"их участникам (тебя там нет).")
            return s + unreachable_note

        if not new_matches:
            if auto:
                no_match_text = (
                    f"🔍 Проверил сразу после сохранения • {current_check_time}\n\n"
                    f"{_summary_text()}"
                )
            else:
                no_match_text = (
                    f"🔔 РЕЗУЛЬТАТ ПРОВЕРКИ (лично тебе) 🔔\n"
                    f"👤 Запустил: {initiator_name}\n"
                    f"🕐 {current_check_time}\n\n"
                    f"{_summary_text()}"
                )
            in_private = update.effective_chat.id == user_id
            try:
                if in_private:
                    await tg(update.message.reply_text, no_match_text)
                else:
                    await tg(context.bot.send_message, chat_id=user_id, text=no_match_text)
            except Exception as e:
                logger.warning(f"Не удалось отправить личный результат инициатору {user_id}: {e}")
                try:
                    await tg(update.message.reply_text,
                        "❌ Не получилось отправить результат тебе в личку.\n"
                        "Напиши боту /start в личных сообщениях и попробуй снова."
                    )
                except Exception:
                    pass
            return

        header = (
            f"🔔 РЕЗУЛЬТАТ ПРОВЕРКИ 🔔\n"
            f"👤 Запустил: {html.escape(initiator_name)}\n"
            f"🕐 {current_check_time}\n\n"
            f"🆕 Новых совпадений: {len(new_matches)} (всего: {len(matches)})\n"
        )
        new_match_uids = set()
        for m in new_matches:
            for u in m["users"]:
                new_match_uids.add(u["uid"])

        try:
            total = len(new_matches)
            if PRIVATE_MATCH_DELIVERY:
                unreachable = {}
                for idx, m in enumerate(new_matches, start=1):
                    token = next_vote_token()
                    m["_token"] = token
                    if user_id in {u["uid"] for u in m["users"]}:
                        per_head = (
                            f"🔔 Новое совпадение • запустил: {html.escape(initiator_name)} "
                            f"• {current_check_time}"
                        )
                    else:
                        per_head = f"🔔 Новое совпадение • {current_check_time}"
                    pre_ratings, pre_finals = carried_ratings_for(
                        m["link"], [u["uid"] for u in m["users"]])
                    state = {
                        "token": token,
                        "copies": [],
                        "link": m["link"],
                        "idx": idx,
                        "users": [{"name": u["name"], "row": u["row"],
                                   "added_at": u["added_at"], "uid": u["uid"],
                                   "username": u.get("username"),
                                   "yang": u.get("yang")} for u in m["users"]],
                        "allowed_uids": [u["uid"] for u in m["users"]],
                        "phase": "initial",
                        "ratings": pre_ratings,
                        "final_ratings": pre_finals,
                        "prefix": per_head,
                        "suffix": "",
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    prev_v = link_verdicts.get(m["link"])
                    if prev_v and (prev_v.get("initial") or prev_v.get("final")):
                        state["carried"] = {"initial": prev_v.get("initial"),
                                            "final": prev_v.get("final"),
                                            "by": prev_v.get("by", "")}
                    match_votes[token] = state
                    if GROUP_MATCH_ARCHIVE:
                        try:
                            gmsg = await tg(context.bot.send_message,
                                chat_id=GROUP_CHAT_ID,
                                text=render_vote_text(state, viewer_uid=GROUP_CHAT_ID),
                                parse_mode="HTML", reply_markup=vote_keyboard(token, "initial", viewer_uid=GROUP_CHAT_ID),
                                disable_web_page_preview=True)
                            state["copies"].append({"chat_id": GROUP_CHAT_ID,
                                                    "message_id": gmsg.message_id})
                        except Exception as e:
                            logger.warning(f"Не удалось отправить архив в группу: {e}")
                    name_by_uid = {u["uid"]: u["name"] for u in m["users"]}
                    delivered_to_member = False
                    member_uids = {u["uid"] for u in m["users"]}
                    for ruid in match_recipient_uids(state):
                        try:
                            pmsg = await tg(context.bot.send_message,
                                chat_id=ruid, text=render_vote_text(state, viewer_uid=ruid),
                                parse_mode="HTML", reply_markup=vote_keyboard(token, "initial", viewer_uid=ruid),
                                disable_web_page_preview=True)
                            state["copies"].append({"chat_id": ruid,
                                                    "message_id": pmsg.message_id})
                            if ruid in member_uids:
                                delivered_to_member = True
                        except Exception as e:
                            nm = (name_by_uid.get(ruid)
                                  or (user_data.get(ruid, {}) or {}).get("name")
                                  or str(ruid))
                            unreachable[ruid] = nm
                            logger.warning(f"Не удалось отправить личное совпадение {ruid}: {e}")
                    if delivered_to_member:
                        reported_matches[m["link"]] = _match_sig(m["users"])
                    save_match_votes()
                note = ""
                if unreachable:
                    who = ", ".join(sorted(set(unreachable.values())))
                    note = ("\n⚠️ Не дошло до: " + who +
                            "\n(они не писали боту в личку — попроси их нажать /start)")
                head = "🔍 Автопроверка после сохранения\n\n" if auto else ""
                await _say(head + _summary_text(note))
            else:
                status = format_status_block(all_users, new_match_uids, mention=True)
                for idx, m in enumerate(new_matches, start=1):
                    token = next_vote_token()
                    m["_token"] = token
                    pre_ratings, pre_finals = carried_ratings_for(
                        m["link"], [u["uid"] for u in m["users"]])
                    state = {
                        "token": token,
                        "chat_id": GROUP_CHAT_ID,
                        "message_id": None,
                        "link": m["link"],
                        "idx": idx,
                        "users": [{"name": u["name"], "row": u["row"],
                                   "added_at": u["added_at"], "uid": u["uid"],
                                   "username": u.get("username"),
                                   "yang": u.get("yang")} for u in m["users"]],
                        "allowed_uids": [u["uid"] for u in m["users"]],
                        "phase": "initial",
                        "ratings": pre_ratings,
                        "final_ratings": pre_finals,
                        "prefix": header if idx == 1 else "",
                        "suffix": status if idx == total else "",
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    prev_v = link_verdicts.get(m["link"])
                    if prev_v and (prev_v.get("initial") or prev_v.get("final")):
                        state["carried"] = {"initial": prev_v.get("initial"),
                                            "final": prev_v.get("final"),
                                            "by": prev_v.get("by", "")}
                    match_votes[token] = state
                    msg = await tg(context.bot.send_message,
                        chat_id=GROUP_CHAT_ID,
                        text=render_vote_text(state, viewer_uid=GROUP_CHAT_ID),
                        parse_mode="HTML", reply_markup=vote_keyboard(token, "initial", viewer_uid=GROUP_CHAT_ID),
                        disable_web_page_preview=True)
                    state["message_id"] = msg.message_id
                    reported_matches[m["link"]] = _match_sig(m["users"])
                    save_match_votes()
                await tg(update.message.reply_text, "✅ Результат проверки отправлен в общий чат!")
                await send_personal_match_copies(context, new_matches, len(matches), initiator_name, current_check_time, user_id)
            save_reported_matches(reported_matches)
            await _ach_credit_matches(context.bot, new_matches)
            undelivered = [m for m in new_matches if m["link"] not in reported_matches]
            if undelivered:
                logger.warning(
                    f"Не доставлено совпадений: {len(undelivered)} — "
                    f"следующая проверка попробует снова.")
            save_data()
        except Exception as e:
            logger.error(f"Ошибка отправки совпадений: {e}")
            try:
                await tg(update.message.reply_text, f"❌ Не удалось отправить результат.\nОшибка: {str(e)[:100]}")
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Ошибка в check_matches: {e}")
        try:
            await tg(update.message.reply_text, "❌ Ошибка при проверке совпадений")
        except Exception:
            pass


async def _ach_credit_matches(bot, new_matches):
    """Зачёт совпадений участникам доставленных совпадений.

    Зачёт идёт НЕЗАВИСИМО по каждому участнику: считаем совпадение новым для
    конкретного uid только если именно ЕГО собственная запись (uid@added_at)
    не встречалась в prev_sig раньше. Иначе, если один участник пересдал сессию
    (новый added_at того же урла), а у второго ничего не изменилось — второй
    получал бы повторный зачёт на пустом месте только из-за того, что общая
    сигнатура пары изменилась. Раньше зачёт шёл по общей сигнатуре на всю пару
    сразу — отсюда и рассинхрон в счётчиках.

    "p" — список партнёров (не set!) — при нескольких совпадениях с одним и тем
    же партнёром в одной проверке партнёр должен засчитаться столько раз,
    сколько было реальных совпадений, а не один раз на всю проверку."""
    per_uid = {}
    for m in new_matches:
        if m["link"] not in reported_matches:
            continue
        prev_sig = m.get("_prev_sig") or set()
        uids = [u["uid"] for u in m["users"]]
        token = m.get("_token")
        state = match_votes.get(token) if token else None
        credit_map = {}
        for u in m["users"]:
            own_key = f"{u['uid']}@{u.get('added_at', '')}"
            if own_key in prev_sig:
                continue
            muid = u["uid"]
            agg = per_uid.setdefault(muid, {"n": 0, "p": []})
            agg["n"] += 1
            partners_for_this_match = [x for x in uids if x != muid]
            agg["p"].extend(partners_for_this_match)
            # запоминаем ровно за этот матч/токен, кому и с кем начислено —
            # без этого отмена отправки не может откатить зачёт совпадения.
            credit_map[str(muid)] = partners_for_this_match
        if state is not None and credit_map:
            state["match_credit"] = credit_map
    for muid, agg in per_uid.items():
        try:
            ach_on_matches(muid, agg["n"], agg["p"])
            await ach_award(bot, muid)
        except Exception as e:
            logger.warning(f"Ачивки: зачёт совпадений {muid}: {e}")


async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text, "❌ Ты ещё не зарегистрирован!", reply_markup=get_start_keyboard())
        return
    if await _handle_skill_gate(update, context, update.message.text or ""):
        return WAITING_FOR_LINKS
    try:
        name = user_data[user_id]["name"]
        user_data[user_id]["links"] = {}
        user_data[user_id]["updated_at"] = datetime.now().isoformat()
        save_data()
        await tg(update.message.reply_text,
            f"🔄 {name}, ты начал новую сессию!\n\nОтправь новые ссылки (до {MAX_LINKS} штук):",
            reply_markup=get_main_keyboard(user_id))
    except Exception as e:
        logger.error(f"Ошибка в new_session: {e}")
        await tg(update.message.reply_text, "❌ Ошибка при сбросе сессии")
    return WAITING_FOR_LINKS


async def undo_last_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text, "❌ Ты ещё не зарегистрирован!", reply_markup=get_start_keyboard())
        return
    sub = _peek_submission(user_id)
    if not sub or (not sub.get("added_urls") and not sub.get("touched")
                   and not sub.get("skill_snapshot")):
        await tg(update.message.reply_text,
            "↩️ Отменять нечего: после прошлой отправки новых ссылок в историю не добавлялось "
            "и навык не менялся.\n"
            "Отменить можно только отправку, которая реально что-то изменила.",
            reply_markup=get_main_keyboard(user_id))
        return
    try:
        removed_urls = list(sub.get("added_urls") or [])
        removed_set = set(removed_urls)

        hist = user_links_history.get(user_id) or []
        if not isinstance(hist, list):
            hist = []
        user_links_history[user_id] = [h for h in hist
                                       if isinstance(h, dict)
                                       and h.get("url") not in removed_set]

        by_url = {h.get("url"): h for h in user_links_history[user_id]}
        for t in (sub.get("touched") or []):
            rec = by_url.get(t.get("url"))
            if not rec:
                continue
            rec["row"] = t.get("row")
            rec["added_at"] = t.get("added_at")
            rec["yang"] = t.get("yang")

        prev_links = sub.get("prev_links") or {}
        user_data[user_id]["links"] = (
            {int(k): v for k, v in prev_links.items()} if prev_links else {}
        )
        user_data[user_id]["updated_at"] = datetime.now().isoformat()

        skill_restored = bool(sub.get("skill_snapshot"))
        _restore_skill_snapshot(user_id, sub.get("skill_snapshot"))

        _pop_submission(user_id)
        ach_on_undo(user_id, len(removed_urls), sub.get("at"))
        save_data()
        save_user_links_history(user_links_history)

        deleted_msgs = 0
        cleaned_links = 0
        for link in removed_urls:
            remaining = [
                uid for uid, data in user_data.items()
                if isinstance(data, dict)
                and data.get("registered")
                and data.get("name") not in BUTTON_NAMES
                and any(isinstance(h, dict) and h.get("url") == link
                        for h in (user_links_history.get(uid) or [])
                        if isinstance(user_links_history.get(uid), list))
            ]
            still_match = len(remaining) >= 2

            if still_match:
                reported_matches[link] = _sig_for_uids(link, remaining)
            else:
                reported_matches.pop(link, None)
                link_verdicts.pop(link, None)
                cleaned_links += 1

            for token in list(match_votes.keys()):
                state = match_votes.get(token)
                if not state or state.get("link") != link:
                    continue
                credit_map = state.get("match_credit") or {}
                vote_credit = state.get("vote_credit") or {}
                users = [u for u in state.get("users", []) if u.get("uid") != user_id]
                if still_match and len(users) >= 2:
                    state["users"] = users
                    state["allowed_uids"] = [u["uid"] for u in users]
                    for key in ("ratings", "final_ratings"):
                        d = state.get(key)
                        if isinstance(d, dict):
                            d.pop(str(user_id), None)
                            d.pop(user_id, None)
                    state["token"] = token
                    # матч остался жив для остальных — снимаем зачёт совпадения
                    # только у того, кто отменил отправку (его строки в матче больше нет).
                    my_partners = credit_map.pop(str(user_id), None)
                    if my_partners is not None:
                        ach_on_matches_undo(user_id, 1, my_partners)
                    # его же оценки на этот матч (ratings.pop выше) больше не валидны —
                    # снимаем зачёт голосов ровно за них, по обеим фазам.
                    for phase_key in ("init", "final"):
                        ph = vote_credit.get(phase_key) or {}
                        my_vote_credit = ph.pop(str(user_id), None)
                        if my_vote_credit:
                            ach_on_vote_undo(user_id, my_vote_credit)
                    await refresh_match_message(context.bot, state)
                else:
                    deleted_msgs += await delete_match_copies(context.bot, state)
                    match_votes.pop(token, None)
                    # матч целиком инвалидирован (меньше 2 участников осталось) —
                    # снимаем зачёт совпадения у ВСЕХ, кому он был начислен по этому
                    # токену, а не только у того, кто нажал "отменить".
                    for uid_s, partners in credit_map.items():
                        try:
                            c_uid = int(uid_s)
                        except (TypeError, ValueError):
                            continue
                        ach_on_matches_undo(c_uid, 1, partners)
                    # матча больше нет вообще — все голоса по нему (обе фазы, все,
                    # кто голосовал) тоже больше не за что засчитывать.
                    for phase_key in ("init", "final"):
                        ph = vote_credit.get(phase_key) or {}
                        for uid_s, credit in ph.items():
                            try:
                                c_uid = int(uid_s)
                            except (TypeError, ValueError):
                                continue
                            ach_on_vote_undo(c_uid, credit)

        save_reported_matches(reported_matches)
        save_link_verdicts()
        save_match_votes()

        skill_line = "\n• Навык откачен к состоянию до этой отправки" if skill_restored else ""
        when = _fmt_local(sub.get("at", ""), user_id) or sub.get("at", "время неизвестно")
        shown_urls = list(sub.get("urls") or sub.get("added_urls") or [])
        urls_block = ""
        if shown_urls:
            urls_block = "\n\nОтменённая пачка:\n" + "\n".join(
                f"{i}. {u}" for i, u in enumerate(shown_urls[:MAX_LINKS], start=1))
        left = len(user_data.get(user_id, {}).get("submissions") or [])
        left_line = f"\n• Можно отменить ещё отправок: {left}" if left else ""
        await tg(update.message.reply_text,
            f"↩️ Отменена отправка от {when}.\n"
            f"• Убрано ссылок из истории: {len(removed_urls)}\n"
            f"• Полностью снято совпадений: {cleaned_links}\n"
            f"• Удалено сообщений в чате: {deleted_msgs}"
            f"{skill_line}{left_line}{urls_block}",
            reply_markup=get_main_keyboard(user_id))
    except Exception as e:
        logger.error(f"Ошибка в undo_last_submission: {e}")
        await tg(update.message.reply_text, "❌ Ошибка при отмене последней отправки")


async def full_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await tg(update.message.reply_text, "⛔ У вас нет прав на эту команду.")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💥 Да, стереть всё", callback_data="fr:yes"),
        InlineKeyboardButton("↩️ Отмена", callback_data="fr:no"),
    ]])
    await tg(update.message.reply_text,
        f"⚠️ <b>Полный сброс</b>\n\n"
        f"Будут безвозвратно удалены: {len(user_data)} участников, "
        f"вся история ссылок, все оценки и совпадения.\n"
        f"Отменить это будет нельзя.\n\n"
        f"Точно продолжаем?",
        parse_mode="HTML", reply_markup=kb)


async def full_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = q.from_user.id
    if user_id not in ADMIN_IDS:
        await tg_answer(q, "⛔ Только администратор.", show_alert=True)
        return
    if q.data != "fr:yes":
        await tg_answer(q, "Сброс отменён ✅", show_alert=True)
        try:
            await tg(context.bot.edit_message_text,
                     chat_id=q.message.chat_id, message_id=q.message.message_id,
                     text="↩️ Полный сброс отменён. Данные на месте.")
        except Exception:
            pass
        return
    await tg_answer(q, "Сбрасываю…")
    update = _ResetUpdateShim(q)
    try:
        global user_data, user_links_history, reported_matches, match_votes, link_verdicts
        global achievements
        for state in list(match_votes.values()):
            try:
                await delete_match_copies(context.bot, state)
            except Exception:
                pass
        user_data = {}
        user_links_history = {}
        reported_matches = {}
        match_votes = {}
        link_verdicts = {}
        achievements = {}
        save_achievements()
        save_data()
        save_user_links_history(user_links_history)
        save_reported_matches(reported_matches)
        save_match_votes()
        save_link_verdicts()
        await tg(update.message.reply_text,
            "💥 ПОЛНЫЙ СБРОС ВСЕХ ДАННЫХ!\nВсе пользователи удалены. Каждый должен заново нажать /start")
        try:
            await tg(context.bot.send_message, chat_id=GROUP_CHAT_ID,
                                           text="💥 Администратор сбросил все данные. Все участники удалены.")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Ошибка в full_reset: {e}")
        await tg(update.message.reply_text, "❌ Ошибка при полном сбросе")


class _ResetUpdateShim:

    def __init__(self, q):
        self._q = q
        self.message = q.message
        self.effective_user = q.from_user


async def _send_bot_ready_broadcast(bot, text=None, uids=None):
    if text is None:
        text = "✅ <b>Бот включился/перезагрузился и готов к работе</b>\nМожете отправлять сессии."
    if uids is None:
        uids = ADMIN_IDS
    sent, failed = 0, 0
    for uid in list(uids):
        try:
            await tg(bot.send_message, chat_id=uid, text=text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"broadcast_bot_ready: не доставлено {uid}: {e}")
    logger.info(f"📣 'Бот включился/перезагрузился и готов к работе' разослана: {sent} ок, {failed} ошибок")
    return sent, failed


async def broadcast_bot_ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not can_broadcast(user_id):
        await tg(update.message.reply_text, "⛔ У вас нет прав на эту команду.")
        return
    try:
        button_text = "🏃 <b>БЕЕЕЕГОМ ДЕЛАТЬ СЕССИИ, ПРИШЛИ РОДНЫЕ</b>"
        sent, failed = await _send_bot_ready_broadcast(context.bot, text=button_text, uids=access_users("approved"))
        await tg(update.message.reply_text, f"📣 Рассылка отправлена.\n✅ Успешно: {sent}\n❌ Ошибок: {failed}")
    except Exception as e:
        logger.error(f"Ошибка в broadcast_bot_ready: {e}")
        await tg(update.message.reply_text, "❌ Ошибка при рассылке")


def _skill_notify_on(user_id) -> bool:
    return bool(user_data.get(user_id, {}).get("skill_notify", True))


async def _sync_skill_commands(bot, user_id: int, enabled: bool) -> bool:
    tail = SKILL_OFF_COMMAND if enabled else SKILL_ON_COMMAND
    commands = [BotCommand(c, d) for (c, d) in [*BASE_COMMANDS, tail]]
    try:
        await tg(bot.set_my_commands, commands=commands,
                 scope=BotCommandScopeChat(chat_id=user_id))
        return True
    except Exception as e:
        logger.warning(f"skill_notify: не смог обновить меню команд для {user_id}: {e}")
        return False


async def _set_skill_notify(update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text,
            "👋 Сначала нажми /start.", reply_markup=get_start_keyboard())
        return

    user_data[user_id]["skill_notify"] = enabled
    save_data()
    synced = await _sync_skill_commands(context.bot, user_id, enabled)

    if enabled:
        msg = ("📈 Уведомления о навыке включены.\n"
               "Расширение снова будет присылать изменения навыка.")
    else:
        msg = ("📉 Уведомления о навыке выключены.\n"
               "Расширение сверяется с этим переключателем перед каждой отправкой, "
               "так что изменения навыка приходить перестанут сразу.")
    if not synced:
        msg += ("\n\n⚠️ Не удалось достучаться до Telegram — расширение узнает "
                "о переключении не сразу. Нажми кнопку ещё раз чуть позже.")

    await tg(update.message.reply_text, msg, reply_markup=get_main_keyboard(user_id))


async def toggle_skill_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text,
            "👋 Сначала нажми /start.", reply_markup=get_start_keyboard())
        return
    await _set_skill_notify(update, context, not _skill_notify_on(user_id))


async def skill_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_skill_notify(update, context, True)


async def skill_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_skill_notify(update, context, False)


def _auto_check_on(user_id) -> bool:
    d = user_data.get(user_id)
    if not isinstance(d, dict):
        return AUTO_CHECK_DEFAULT
    return bool(d.get("auto_check", AUTO_CHECK_DEFAULT))


async def toggle_auto_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text,
            "👋 Сначала нажми /start.", reply_markup=get_start_keyboard())
        return
    new_state = not _auto_check_on(user_id)
    user_data[user_id]["auto_check"] = new_state
    save_data()
    if new_state:
        msg = ("⚡ Автопроверка включена.\n"
               "Сразу после сохранения ссылок бот сам гоняет проверку совпадений — "
               "кнопку жать не нужно.")
    else:
        msg = ("🐢 Автопроверка выключена.\n"
               "Ссылки просто сохраняются. Совпадения ищутся только когда ты сам "
               "нажмёшь «✅ Проверить совпадения».")
    await tg(update.message.reply_text, msg, reply_markup=get_main_keyboard(user_id))


async def auto_check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await toggle_auto_check(update, context)


async def toggle_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text,
            "👋 Сначала нажми /start.", reply_markup=get_start_keyboard())
        return
    new_state = not user_data[user_id].get("notify", True)
    user_data[user_id]["notify"] = new_state
    save_data()
    if new_state:
        msg = "🔔 Уведомления включены.\nБуду присылать тебе в личку новые задания «Разметка поисковых сессий»."
    else:
        msg = "🔕 Уведомления выключены.\nБольше не буду присылать тебе задания «Разметка поисковых сессий» в личку."
    await tg(update.message.reply_text, msg, reply_markup=get_main_keyboard(user_id))


async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📋 Мои ссылки":
        await show_my_links(update, context)
    elif text == "🔥 Актуальные сессии":
        await show_active_sessions(update, context)
    elif text == "👥 Участники":
        await show_participants(update, context)
    elif text == "✅ Проверить совпадения":
        await check_matches(update, context)
    elif text == "🔄 Новая сессия":
        await new_session(update, context)
    elif text == "↩️ Отменить отправку":
        await undo_last_submission(update, context)
    elif text == ACH_BTN:
        await show_achievements(update, context)
    elif text == "📣 Рассылка":
        await broadcast_bot_ready(update, context)
    elif text == "🧽 Очистить старое":
        await cleanup_now(update, context)
    elif text == "💥 Полный сброс":
        await full_reset(update, context)
    elif text in (NOTIFY_ON_BTN, NOTIFY_OFF_BTN):
        await toggle_notify(update, context)
    elif text in (SKILL_ON_BTN, SKILL_OFF_BTN):
        await toggle_skill_notify(update, context)
    elif text in (AUTO_ON_BTN, AUTO_OFF_BTN):
        await toggle_auto_check(update, context)
    elif text == "🚀 Начать работу":
        await start_work(update, context)
    elif text == "❌ Отмена":
        await tg(update.message.reply_text, "❌ Действие отменено", reply_markup=get_start_keyboard())


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not is_user_active(user_id):
        await tg(update.message.reply_text,
            "👋 Привет! Нажми /start, чтобы начать работу.",
            reply_markup=get_start_keyboard())
        return
    if await _handle_skill_gate(update, context, text):
        return
    if text in MENU_BUTTONS:
        await handle_main_menu(update, context)
        return
    if user_data.get(user_id, {}).get("awaiting_new_name"):
        await change_name_save(update, context)
        return
    await _save_links_from_text(update, context, text)


# ---------- экраны ----------


def _ach_total_tiers():
    return sum(len(a["tiers"]) for a in ACHIEVEMENTS_DEF)


def _ach_home_text(uid, viewer_uid=None):
    rec, m, items = _ach_state(uid)
    lvl, cur_xp, need_xp = _ach_level(_ach_xp(rec))
    got = _ach_tiers_taken(rec)
    total = _ach_total_tiers()
    name = (user_data.get(uid, {}) or {}).get("name") or "Участник"
    title = "🏆 <b>ДОСТИЖЕНИЯ</b>"
    if viewer_uid is not None and viewer_uid != uid:
        title = f"👤 <b>ПРОФИЛЬ</b>"
        if viewer_uid in ADMIN_IDS and _ach_is_optout(uid):
            title += " 🙈 <i>(скрыт из топа)</i>"
    lines = [
        f"{title} — {html.escape(str(name))}",
        "",
        f"⭐ Уровень {lvl}  •  {rec.get('xp', 0)} XP",
        f"{_ach_bar(cur_xp, need_xp)} {cur_xp}/{need_xp} до {lvl + 1} уровня",
        f"🏅 Взято тиров: {got} из {total}",
        "",
        f"📦 Сессий: {m['sessions_total']}  •  ⚖️ Оценок: {m['votes_total']}  •  "
        f"🎯 Совпадений: {m['matches_total']}",
        f"🔥 Лучшая серия: {m['streak_days']} дн.  •  📈 Пик навыка: {_ach_fmt_val(m['skill_peak'])}",
    ]
    near = _ach_nearest(items)
    if near:
        lines.append("")
        lines.append("<b>Ближе всего:</b>")
        for i in near:
            a = i["a"]
            lines.append(
                f"{_ach_tier_icon(a, i['done'])} {html.escape(a['name'])} — "
                f"{_ach_bar(i['val'], i['next'])} "
                f"{_ach_fmt_val(i['val'])}/{_ach_fmt_val(i['next'])}"
            )
    return "\n".join(lines)


def _ach_target_suffix(uid, viewer_uid):
    """Добавляет ':<uid>' к callback_data, если смотрим чужой профиль."""
    return "" if viewer_uid is None or viewer_uid == uid else f":{uid}"


def _ach_home_kb(uid, viewer_uid=None):
    sfx = _ach_target_suffix(uid, viewer_uid)
    own = viewer_uid is None or viewer_uid == uid
    if own:
        on = (user_data.get(uid, {}) or {}).get("ach_notify", True)
        optout = _ach_is_optout(uid)
        rows = [[InlineKeyboardButton("🏅 Все достижения", callback_data="ach:cat:0"),
                 InlineKeyboardButton("📊 Статистика", callback_data="ach:stats")],
                [InlineKeyboardButton("🏆 Топ участников", callback_data="ach:top"),
                 InlineKeyboardButton("🔔 Уведомления: вкл" if on else "🔕 Уведомления: выкл",
                                      callback_data="ach:ntf")],
                [InlineKeyboardButton("🙈 Скрыть себя из топа" if not optout
                                      else "👁 Скрыт из топа — нажми, чтобы показаться",
                                      callback_data="ach:optout")]]
        return InlineKeyboardMarkup(rows)
    # чужой профиль: обычно только достижения с прогресс-барами, без статистики;
    # админу дополнительно доступна полная статистика — в том числе для скрытых
    top_row = [InlineKeyboardButton("🏅 Достижения", callback_data=f"ach:cat:0{sfx}")]
    if viewer_uid in ADMIN_IDS:
        top_row.append(InlineKeyboardButton("📊 Статистика", callback_data=f"ach:stats{sfx}"))
    rows = [top_row]
    rows.append([InlineKeyboardButton("🏆 Топ участников", callback_data="ach:top"),
                 InlineKeyboardButton("👤 Мой профиль", callback_data="ach:home")])
    return InlineKeyboardMarkup(rows)


def _ach_cat_text(uid, cat_idx, viewer_uid=None):
    rec, m, items = _ach_state(uid)
    cat_idx = max(0, min(cat_idx, len(ACH_CATEGORIES) - 1))
    code, title = ACH_CATEGORIES[cat_idx]
    lines = [f"<b>{html.escape(title)}</b>"]
    if viewer_uid is not None and viewer_uid != uid:
        name = (user_data.get(uid, {}) or {}).get("name") or "Участник"
        lines[0] += f" — {html.escape(str(name))}"
    lines.append("")
    for i in items:
        a = i["a"]
        if a["cat"] != code:
            continue
        if a.get("hidden") and i["done"] == 0:
            lines.append("🔒 <i>Скрытое достижение</i> — откроется само")
            lines.append("")
            continue
        icon = _ach_tier_icon(a, max(0, i["done"] - 1)) if i["done"] else "▫️"
        got = f"{i['done']}/{i['max']}" if not a.get("hidden") else ("взято" if i["done"] else "—")
        lines.append(f"{icon} <b>{html.escape(a['name'])}</b>  <code>{got}</code>")
        lines.append(f"   {html.escape(a['desc'])}")
        if i["next"] is not None:
            lines.append(f"   {_ach_bar(i['val'], i['next'])} "
                         f"{_ach_fmt_val(i['val'])}/{_ach_fmt_val(i['next'])} "
                         f"{html.escape(a.get('unit') or '')}".rstrip())
        else:
            lines.append(f"   ✅ Всё взято ({_ach_fmt_val(i['val'])})")
        lines.append("")
    lines.append(f"Раздел {cat_idx + 1} из {len(ACH_CATEGORIES)}")
    return "\n".join(lines)


def _ach_cat_kb(cat_idx, uid=None, viewer_uid=None):
    sfx = "" if uid is None else _ach_target_suffix(uid, viewer_uid)
    prev_i = (cat_idx - 1) % len(ACH_CATEGORIES)
    next_i = (cat_idx + 1) % len(ACH_CATEGORIES)
    home_cb = "ach:home" if not sfx else f"ach:home{sfx}"
    rows = [
        [InlineKeyboardButton("◀️", callback_data=f"ach:cat:{prev_i}{sfx}"),
         InlineKeyboardButton("🏠 Обзор", callback_data=home_cb),
         InlineKeyboardButton("▶️", callback_data=f"ach:cat:{next_i}{sfx}")],
    ]
    row = []
    for i, (_code, title) in enumerate(ACH_CATEGORIES):
        row.append(InlineKeyboardButton(title.split(" ")[0], callback_data=f"ach:cat:{i}{sfx}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if sfx:
        rows.append([InlineKeyboardButton("🏆 Топ", callback_data="ach:top"),
                     InlineKeyboardButton("👤 Мой профиль", callback_data="ach:home")])
    return InlineKeyboardMarkup(rows)


def _ach_stats_text(uid, viewer_uid=None):
    rec, m, items = _ach_state(uid)
    lvl, cur_xp, need_xp = _ach_level(_ach_xp(rec))
    days = rec.get("days") or {}
    active_days = max(m["active_days"],
                      sum(1 for d in days.values()
                          if isinstance(d, dict) and int(d.get("sessions", 0) or 0) > 0))
    today = _ach_day_key(uid)
    today_d = days.get(today) or {}
    st = rec.get("streak") or {}
    skill_last = None
    for k in sorted(days.keys(), reverse=True):
        v = (days.get(k) or {}).get("skill_last")
        if v is not None:
            skill_last = v
            break
    header = "📊 <b>ОБЩАЯ СТАТИСТИКА</b>"
    if viewer_uid is not None and viewer_uid != uid:
        name = (user_data.get(uid, {}) or {}).get("name") or "Участник"
        header += f" — {html.escape(str(name))}"
    lines = [
        header,
        "",
        f"⭐ Уровень {lvl} • {rec.get('xp', 0)} XP ({cur_xp}/{need_xp} до следующего)",
        f"🏅 Тиров взято: {_ach_tiers_taken(rec)} из {_ach_total_tiers()}",
        "",
        "<b>Сессии</b>",
        f"• Всего: {m['sessions_total']}",
        f"• Сегодня: {int(today_d.get('sessions', 0) or 0)}",
        f"• Лучший день: {m['sessions_day']}",
        f"• Лучшая неделя: {m['sessions_week']}",
        f"• Лучшие 30 дней: {m['sessions_month']}",
        f"• Лучший час: {m['sessions_hour']}",
        f"• Ссылок размечено: {m['urls_total']} (полных пачек: {m['full_packs']})",
        f"• Отправок всего: {m['packs']} (полных по {MAX_LINKS}: {m['full_packs']})",
        f"• Активных дней: {active_days} • стаж: {m['tenure_days']} дн.",
        "",
        "<b>Серии</b>",
        f"• Текущая: {int(st.get('cur', 0) or 0)} дн.  •  Рекорд: {m['streak_days']} дн.",
        f"• Недель подряд со 100+: {m['weeks_100']} • просто активных недель подряд: {m['weeks_active']}",
        f"• Лучший месяц: {m['month_days']} активных дней",
        f"• Отправок без отмены подряд: {int((rec.get('counters') or {}).get('clean_streak', 0) or 0)} "
        f"(рекорд {m['clean_streak']})",
        "",
        "<b>Оценки</b>",
        f"• Всего: {m['votes_total']}  •  финальных: {m['final_votes']}",
        f"• За лучший день: {m['votes_day']} • за лучшие 2 дня: {m['votes_2d']}",
        f"• Быстрых (&lt;{ACH_FAST_VOTE_SECONDS // 60} мин): {m['fast_votes']} • "
        f"молний (&lt;{ACH_LIGHTNING_SECONDS} сек): {m['lightning_votes']}",
        f"• Дней подряд с оценками: {m['vote_streak_days']}",
        "",
        "<b>Навык</b>",
        f"• Пик: {_ach_fmt_val(m['skill_peak'])}"
        + (f"  •  последний: {_ach_fmt_val(skill_last)}" if skill_last is not None else ""),
        f"• Дней целиком на 90+: {m['skill_hi_days']}",
        f"• Дней подряд без падения: {m['skill_nofall']} • дней подряд на 90+: {m['skill90_streak']}",
        f"• Подъёмов навыка: {m['skill_ups']} • камбэков с 80−: {m['comebacks']}",
        "",
        "<b>Совпадения</b>",
        f"• Всего: {m['matches_total']}  •  разных участников: {m['partners_unique']}",
        f"• Лучший день: {m['matches_day']} • лучшая проверка: {m['matches_burst']}",
        f"• Чаще всего с одним участником: {_ach_partner_best_line(rec, m['partner_best'])}",
        "",
        "<b>Режим</b>",
        f"• Ночных дней (00–05): {m['night_days']}  •  ранних (до 07): {m['early_days']}",
        f"• Выходных с работой: {m['weekend_days']} • часов суток освоено: {m['hours_seen']}/24",
        f"• Самый длинный день: {m['day_hours']} разных часов с отправками",
        "",
        "<i>Счётчики достижений копятся отдельно и автоочисткой ссылок "
        f"(TTL {LINKS_TTL_DAYS} дн.) не сбрасываются.</i>",
    ]
    return "\n".join(lines)


def _ach_top_rows(uid, include_hidden=False):
    """Строки топа. Обычному участнику чужие скрытые не показываются, но свою
    собственную строку он видит всегда — так можно сравнить себя с топом,
    как если бы участвовал, оставаясь при этом невидимым для остальных.
    include_hidden=True (только для админа) показывает вообще всех.

    Результат кэшируется до следующей записи в achievements — см. комментарий
    у _ach_top_rows_cache."""
    ck = (uid, bool(include_hidden), _ach_data_version)
    hit = _ach_top_rows_cache.get(ck)
    if hit is not None:
        return hit
    rows = []
    for other_uid, rec in achievements.items():
        if not isinstance(rec, dict):
            continue
        d = user_data.get(other_uid) or {}
        if not d.get("registered"):
            continue
        optout = bool(d.get("ach_optout"))
        if optout and not include_hidden and other_uid != uid:
            continue
        name = d.get("name") or str(other_uid)
        if name in BUTTON_NAMES:
            continue
        rec = _ach_rec(other_uid)
        xp = _ach_xp(rec)
        m = _ach_metrics(rec)
        rows.append({"uid": other_uid, "name": name, "xp": xp, "optout": optout,
                     "tiers": _ach_tiers_taken(rec),
                     "sessions": m["sessions_total"], "votes": m["votes_total"],
                     "matches": m["matches_total"], "skill_peak": m["skill_peak"],
                     "streak_days": m["streak_days"], "sessions_day": m["sessions_day"],
                     "lightning_votes": m["lightning_votes"],
                     "comebacks": m["comebacks"],
                     "night_days": m["night_days"], "early_days": m["early_days"],
                     "matches_burst": m["matches_burst"],
                     "clean_streak": m["clean_streak"], "no_undo_days": m["no_undo_days"],
                     "skill_nofall": m["skill_nofall"],
                     "partner_best": m["partner_best"],
                     "partner_best_uid": _ach_partner_best_uid(rec)[0],
                     "partner_counts": rec.get("partner_counts") or {},
                     "tenure_days": m["tenure_days"],
                     "urls_total": m["urls_total"], "final_votes": m["final_votes"],
                     "partners_unique": m["partners_unique"], "day_hours": m["day_hours"],
                     "day_hours_run": m["day_hours_run"],
                     "sessions_hour": m["sessions_hour"], "sessions_week": m["sessions_week"],
                     "sessions_month": m["sessions_month"], "matches_day": m["matches_day"],
                     "votes_day": m["votes_day"], "full_packs": m["full_packs"],
                     "full_streak": m["full_streak"], "fast_votes": m["fast_votes"],
                     "skill_ups": m["skill_ups"], "skill90_streak": m["skill90_streak"],
                     "skill_hi_days": m["skill_hi_days"], "weekend_days": m["weekend_days"],
                     "hours_seen": m["hours_seen"], "night_streak": m["night_streak"],
                     "month_days": m["month_days"], "active_days": m["active_days"],
                     "vote_streak_days": m["vote_streak_days"]})
    rows.sort(key=lambda r: (-r["xp"], -r["tiers"], r["name"].lower()))
    my_place = next((i for i, r in enumerate(rows, start=1) if r["uid"] == uid), None)
    if len(_ach_top_rows_cache) >= ACH_TOP_CACHE_MAX:
        _ach_top_rows_cache.clear()
    _ach_top_rows_cache[ck] = (rows, my_place)
    return rows, my_place


# Каталог рекордов.
#   key   — поле строки топа (см. _ach_top_rows)
#   min   — порог показа: рекорд из одного дня/одной штуки никому не интересен
#   core  — попадает в короткий блок; остальное открывается кнопкой "Все рекорды"
ACH_RECORD_CATS = [
    {"key": "sessions", "label": "\U0001F4E6 Больше всего сессий за всё время",
     "fmt": "{v}", "min": 1, "core": True},
    {"key": "matches", "label": "\U0001F3AF Больше всего совпадений найдено",
     "fmt": "{v}", "min": 1, "core": True},
    {"key": "votes", "label": "\u2696\ufe0f Больше всего оценок выставлено",
     "fmt": "{v}", "min": 1, "core": True},
    {"key": "urls_total", "label": "\U0001F517 Больше всего размечено ссылок всего",
     "fmt": "{v}", "min": 1, "core": True},
    {"key": "sessions_day", "label": "\U0001F525 Рекорд сессий за один день",
     "fmt": "{v}", "min": 3, "core": True},
    {"key": "streak_days", "label": "\U0001F4C5 Самая длинная серия дней подряд",
     "fmt": "{v} дн.", "min": 3, "core": True},
    {"key": "skill_peak", "label": "\U0001F4C8 Самый высокий пик навыка",
     "fmt": "{v}", "min": 1, "core": True},
    {"key": "matches_burst", "label": "\U0001F3AF Залп совпадений за одну проверку",
     "fmt": "{v}", "min": 3, "core": True},
    {"key": "lightning_votes", "label": "\u26A1 Больше всего молниеносных оценок",
     "fmt": "{v}", "min": 3, "core": True},
    {"key": "partners_unique", "label": "\U0001F9E9 Самый широкий круг совпадений",
     "fmt": "{v} чел.", "min": 3, "core": True},

    # --- полный список -----------------------------------------------------
    {"key": "sessions_hour", "label": "\U0001F680 Больше всего сессий за один час",
     "fmt": "{v}", "min": 3},
    {"key": "sessions_week", "label": "\U0001F4C6 Рекорд сессий за неделю",
     "fmt": "{v}", "min": 10},
    {"key": "sessions_month", "label": "\U0001F5D3 Рекорд сессий за 30 дней",
     "fmt": "{v}", "min": 30},
    {"key": "full_packs", "label": "\U0001F590 Больше всего полных пачек по 5",
     "fmt": "{v}", "min": 5},
    {"key": "full_streak", "label": "\U0001F590 Самая длинная серия полных пачек",
     "fmt": "{v} подряд", "min": 5},
    {"key": "matches_day", "label": "\U0001F3AF Больше всего совпадений за день",
     "fmt": "{v}", "min": 3},
    {"key": "votes_day", "label": "\u2696\ufe0f Больше всего оценок за один день",
     "fmt": "{v}", "min": 5},
    {"key": "final_votes", "label": "\U0001F3C1 Больше всего финальных вердиктов",
     "fmt": "{v}", "min": 3},
    {"key": "fast_votes", "label": "\u23E9 Больше всего быстрых оценок",
     "fmt": "{v}", "min": 5},
    {"key": "vote_streak_days", "label": "\u2696\ufe0f Дней подряд с оценками",
     "fmt": "{v} дн.", "min": 3},
    {"key": "skill_ups", "label": "\U0001F4C8 Больше всего подъёмов навыка",
     "fmt": "{v}", "min": 3},
    {"key": "skill90_streak", "label": "\U0001F3C6 Дольше всех держал навык 90+",
     "fmt": "{v} дн.", "min": 2},
    {"key": "skill_hi_days", "label": "\U0001F48E Больше всего дней целиком на 90+",
     "fmt": "{v} дн.", "min": 2},
    {"key": "skill_nofall", "label": "\U0001F4C8 Дольше всех держал навык без падения",
     "fmt": "{v} дн.", "min": 3},
    {"key": "comebacks", "label": "\U0001F504 Больше всего камбэков навыка",
     "fmt": "{v}", "min": 1},
    {"key": "clean_streak", "label": "\U0001F9F9 Самая длинная серия без единой отмены",
     "fmt": "{v}", "min": 5},
    {"key": "night_days", "label": "\U0001F989 Больше всего ночных дней (00–05)",
     "fmt": "{v}", "min": 2},
    {"key": "night_streak", "label": "\U0001F319 Самая длинная серия ночей подряд",
     "fmt": "{v} дн.", "min": 2},
    {"key": "early_days", "label": "\U0001F305 Больше всего раннего старта (до 07)",
     "fmt": "{v}", "min": 2},
    {"key": "weekend_days", "label": "\U0001F3D6 Больше всех работал в выходные",
     "fmt": "{v} дн.", "min": 2},
    {"key": "day_hours", "label": "\U0001F550 Больше всего рабочих часов за день",
     "fmt": "{v} ч.", "min": 4},
    {"key": "day_hours_run", "label": "\u23F1 Самый длинный день без перерыва",
     "fmt": "{v} ч. подряд", "min": 3},
    {"key": "hours_seen", "label": "\U0001F55B Самый широкий охват суток",
     "fmt": "{v} ч. из 24", "min": 6},
    {"key": "month_days", "label": "\U0001F5D3 Больше всего активных дней в месяце",
     "fmt": "{v} дн.", "min": 5},
    {"key": "active_days", "label": "\u26F3 Больше всего активных дней всего",
     "fmt": "{v}", "min": 3},
    {"key": "tenure_days", "label": "\U0001F396 Ветеран: дольше всех в системе",
     "fmt": "{v} дн.", "min": 3},
]

# Производные рекорды: считаются на лету из тех же полей, ничего не хранят.
# den_min — минимальный знаменатель, иначе новичок с 2 сессиями и 2 совпадениями
# заберёт 100% КПД у того, кто работает месяцами.
ACH_DERIVED_RECS = [
    {"label": "\U0001F441 Самый глазастый (совпадений на сессию)",
     "num": "matches", "den": "sessions", "den_min": 20, "kind": "ratio",
     "min": 0.01, "core": True},
    {"label": "\U0001F3C1 Самая высокая доля финальных вердиктов",
     "num": "final_votes", "den": "votes", "den_min": 20, "kind": "pct", "min": 1},
    {"label": "\U0001F525 Самый плотный темп (сессий в активный день)",
     "num": "sessions", "den": "active_days", "den_min": 3, "kind": "ratio", "min": 0.01},
    {"label": "\U0001F4E6 Самые полные пачки (ссылок на сессию)",
     "num": "urls_total", "den": "sessions", "den_min": 20, "kind": "ratio", "min": 0.01},
]

ACH_REC_MAX_WINNERS = 3   # рекорд с 4+ обладателями — не рекорд, а общее место
ACH_REC_TIE_MIN_ROWS = 4  # правило "половина топа поделила рекорд" ниже смысла не имеет


def _ach_rec_winners(rows, valfn, min_val):
    """Лидеры по одной категории или (None, []) если показывать нечего.

    Отсекаем два вида мусора:
      1) значение ниже порога (серия из 2 дней у всех подряд — не достижение);
      2) массовая ничья — если рекорд поделили больше ACH_REC_MAX_WINNERS
         человек или как минимум половина топа, строка ничего не сообщает.
    """
    best = None
    winners = []
    for r in rows:
        v = valfn(r)
        if v is None or v < min_val:
            continue
        if best is None or v > best:
            best, winners = v, [r]
        elif v == best:
            winners.append(r)
    if best is None:
        return None, []
    if len(winners) > ACH_REC_MAX_WINNERS:
        return None, []
    if len(rows) >= ACH_REC_TIE_MIN_ROWS and len(winners) > 1 and len(winners) * 2 >= len(rows):
        return None, []
    return best, winners


def _ach_rec_plain_val(r, key):
    try:
        return float(r.get(key) or 0)
    except (TypeError, ValueError):
        return None


def _ach_rec_derived_val(r, c):
    try:
        den = float(r.get(c["den"]) or 0)
        num = float(r.get(c["num"]) or 0)
    except (TypeError, ValueError):
        return None
    if den <= 0 or den < c["den_min"]:
        return None
    val = num / den
    # округляем ДО сравнения, иначе 0.9999999 и 1.0 разъедут ничью на пустом месте
    return round(val * 100) if c["kind"] == "pct" else round(val, 2)


def _ach_fmt_ratio(v):
    return f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",") or "0"


def _ach_top_records(rows, full=False):
    out = []
    for c in ACH_RECORD_CATS:
        if not full and not c.get("core"):
            continue
        key = c["key"]
        best, winners = _ach_rec_winners(rows, lambda r, k=key: _ach_rec_plain_val(r, k),
                                         c.get("min", 1))
        if best is None:
            continue
        out.append((c["label"], winners, c["fmt"].format(v=_ach_fmt_val(best))))
    for c in ACH_DERIVED_RECS:
        if not full and not c.get("core"):
            continue
        best, winners = _ach_rec_winners(rows, lambda r, c=c: _ach_rec_derived_val(r, c),
                                         c.get("min", 0))
        if best is None:
            continue
        val = f"{int(best)}%" if c["kind"] == "pct" else _ach_fmt_ratio(best)
        out.append((c["label"], winners, val))
    return out


def _ach_top_best_pair(rows, viewer_uid=None):
    """Пара участников с наибольшим числом совместных совпадений.
    Партнёр должен сам входить в текущий (уже отфильтрованный по видимости) rows —
    иначе скрытый партнёр мог бы "просочиться" в рекорд по имени для третьих лиц.
    Исключение: если пара — это собственная пара смотрящего (viewer_uid), он видит
    её честно, даже если сам скрыт и партнёр тоже скрыт — это его личная статистика.

    Для каждой строки перебираем ВСЕХ её партнёров по убыванию числа совпадений
    (не только персональный топ-1), пока не найдём первого допустимого к показу —
    иначе если и личный топ-партнёр, и топ-партнёр другой стороны пары оба скрыты,
    валидная видимая пара рангом ниже терялась бы целиком вместо честного показа."""
    allowed = {r["uid"] for r in rows}
    best = None
    for r in rows:
        is_own_pair = viewer_uid is not None and r["uid"] == viewer_uid
        counts = r.get("partner_counts") or {}
        candidates = []
        for k, v in counts.items():
            try:
                n = int(v or 0)
                p_uid = int(k)
            except (TypeError, ValueError):
                continue
            if n <= 0:
                continue
            candidates.append((n, p_uid))
        candidates.sort(key=lambda x: -x[0])
        for n, p_uid in candidates:
            if p_uid in allowed or is_own_pair:
                if best is None or n > best[0]:
                    best = (n, r["uid"], r["name"], p_uid)
                break  # лучший из допустимых для этой строки найден — дальше по строке не идём
    if best is None:
        return None
    n, uid_a, name_a, uid_b = best
    d_b = user_data.get(uid_b) or {}
    name_b = d_b.get("name") or str(uid_b)
    return name_a, uid_a, name_b, uid_b, n


def _ach_fit(lines, limit=SAFE_LIMIT):
    """Склеивает строки, не вылезая за лимит Telegram.

    Экран топа редактируется одним сообщением — разбить на несколько нельзя,
    а edit_message_text на 4096+ символов упадёт BadRequest. Полный список
    рекордов при большом составе участников как раз способен туда упереться."""
    out = []
    total = 0
    cut = False
    for ln in lines:
        add = len(ln) + 1
        if total + add > limit - 40:
            cut = True
            break
        out.append(ln)
        total += add
    if cut:
        out.append("")
        out.append("<i>…список обрезан, чтобы влезть в одно сообщение.</i>")
    return "\n".join(out)


def _ach_top_text(uid, include_hidden=False, full_records=False):
    rows, my_place = _ach_top_rows(uid, include_hidden=include_hidden)
    if not rows:
        return "🏆 <b>ТОП УЧАСТНИКОВ</b>\n\nПока пусто — статистика копится с этого момента."
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>ТОП УЧАСТНИКОВ</b>"]
    if include_hidden:
        lines.append("👁 Режим админа: показаны и скрытые из топа участники (🙈)")
    lines += ["", "Нажми на участника, чтобы открыть его профиль.", ""]
    for i, r in enumerate(rows, start=1):
        if i > ACH_TOP_LIMIT:
            continue
        mark = medals[i - 1] if i <= 3 else f"{i}."
        if r["uid"] == uid:
            me = " ← ты (скрыт, видно только тебе)" if r.get("optout") else " ← ты"
        else:
            me = " 🙈" if r.get("optout") else ""
        lvl, _c, _n = _ach_level(r["xp"])
        lines.append(
            f"{mark} <b>{html.escape(str(r['name']))}</b>{me}\n"
            f"    ⭐ ур. {lvl} • {r['xp']} XP • 🏅 {r['tiers']} • "
            f"📦 {r['sessions']} • ⚖️ {r['votes']}"
        )
    if my_place and my_place > ACH_TOP_LIMIT:
        lines.append("")
        lines.append(f"Твоё место: {my_place} из {len(rows)}")
    if not include_hidden and _ach_is_optout(uid):
        lines.append("")
        lines.append("<i>Ты скрыт из топа — эту сводку с твоим местом видишь только ты, "
                      "остальные тебя здесь не увидят.</i>")
    rows_by_uid = {r["uid"]: r for r in rows}
    records = _ach_top_records(rows, full=full_records)
    pair = _ach_top_best_pair(rows, viewer_uid=uid)
    my_rec = _ach_rec(uid)
    my_p_uid, my_p_n = _ach_partner_best_uid(my_rec)
    my_p_line = _ach_partner_best_line(my_rec, my_p_n) if my_p_uid is not None and my_p_n > 0 else None
    if records or pair or my_p_line:
        lines.append("")
        lines.append("🏅 <b>РЕКОРДСМЕНЫ</b>")
        for label, winners, val in records:
            names = []
            for r in winners:
                r_uid = r["uid"]
                me = " (ты)" if r_uid == uid else (" 🙈" if rows_by_uid.get(r_uid, {}).get("optout") else "")
                names.append(f"<b>{html.escape(str(r['name']))}</b>{me}")
            lines.append(f"{label}: {', '.join(names)} — {val}")
        if pair:
            name_a, uid_a, name_b, uid_b, n = pair
            you_a = " (ты)" if uid_a == uid else (" 🙈" if _ach_is_optout(uid_a) else "")
            you_b = " (ты)" if uid_b == uid else (" 🙈" if _ach_is_optout(uid_b) else "")
            lines.append(
                f"🤝 Лучшая пара: <b>{html.escape(str(name_a))}</b>{you_a} + "
                f"<b>{html.escape(str(name_b))}</b>{you_b} — {n} совпадений вместе"
            )
        if my_p_line:
            lines.append(f"👤 Ты чаще всего совпадаешь с: {my_p_line}")
        if not full_records:
            lines.append("")
            lines.append("<i>Это главные категории. «📜 Все рекорды» — полный список.</i>")
    return _ach_fit(lines)


def _ach_top_kb(uid, include_hidden=False, full_records=False):
    rows, _my_place = _ach_top_rows(uid, include_hidden=include_hidden)
    medals = ["🥇", "🥈", "🥉"]
    kb_rows = []
    for i, r in enumerate(rows[:ACH_TOP_LIMIT], start=1):
        mark = medals[i - 1] if i <= 3 else f"{i}."
        label = f"{mark} {r['name']}"
        if r["uid"] == uid:
            label += " (ты)"
        elif r.get("optout"):
            label += " 🙈"
        label = label[:60]
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"ach:home:{r['uid']}")])
    kb_rows.append([InlineKeyboardButton(
        "📉 Свернуть рекорды" if full_records else "📜 Все рекорды",
        callback_data="ach:recfull")])
    if uid in ADMIN_IDS:
        kb_rows.append([InlineKeyboardButton(
            "🙈 Скрыть скрытых из списка" if include_hidden else "👁 Показать скрытых (админ)",
            callback_data="ach:toptoggle")])
    kb_rows.append([InlineKeyboardButton("🏠 Мой профиль", callback_data="ach:home")])
    return InlineKeyboardMarkup(kb_rows)


def _ach_back_kb(uid=None, viewer_uid=None):
    sfx = "" if uid is None else _ach_target_suffix(uid, viewer_uid)
    home_cb = "ach:home" if not sfx else f"ach:home{sfx}"
    rows = [[InlineKeyboardButton("🏠 Обзор", callback_data=home_cb),
             InlineKeyboardButton("🏅 Все достижения", callback_data=f"ach:cat:0{sfx}")]]
    if sfx:
        rows.append([InlineKeyboardButton("🏆 Топ", callback_data="ach:top"),
                     InlineKeyboardButton("👤 Мой профиль", callback_data="ach:home")])
    return InlineKeyboardMarkup(rows)


async def show_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_active(user_id):
        await tg(update.message.reply_text, "❌ Ты ещё не зарегистрирован!",
                 reply_markup=get_start_keyboard())
        return
    # пороги могли поменяться — досчитываем прямо на входе, не ждём следующей отправки
    await ach_award(context.bot, user_id)
    await tg(update.message.reply_text, _ach_home_text(user_id),
             parse_mode="HTML", reply_markup=_ach_home_kb(user_id))


def _ach_resolve_target(uid, raw):
    """Валидирует чужой uid из callback_data. Возвращает (target_uid, ok).
    Админам разрешён просмотр профилей, даже если участник скрыл себя из топа."""
    if raw is None:
        return uid, True
    try:
        target = int(raw)
    except (TypeError, ValueError):
        return uid, False
    if target == uid:
        return uid, True
    d = user_data.get(target) or {}
    if not d.get("registered"):
        return uid, False
    if d.get("ach_optout") and uid not in ADMIN_IDS:
        return uid, False
    return target, True


async def achievements_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    parts = (q.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "home"
    await ach_award(context.bot, uid)

    try:
        text, kb = await _ach_callback_render(uid, action, parts, q)
    except Exception as e:
        logger.exception(f"Ачивки: сбой при построении экрана '{action}' для {uid}")
        await tg_answer(q, "⚠️ Ошибка статистики, уже смотрю логи", show_alert=True)
        return

    try:
        await tg(context.bot.edit_message_text,
                 chat_id=q.message.chat_id, message_id=q.message.message_id,
                 text=text, parse_mode="HTML", reply_markup=kb,
                 disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.exception(f"Ачивки: не удалось обновить экран {uid} (BadRequest)")
            await tg_answer(q, "⚠️ Не удалось обновить экран", show_alert=True)
    except Exception as e:
        logger.exception(f"Ачивки: не удалось обновить экран {uid}")
        await tg_answer(q, "⚠️ Не удалось обновить экран", show_alert=True)


async def _ach_callback_render(uid, action, parts, q):
    if action == "ntf":
        # чужие настройки уведомлений недоступны — эта кнопка есть только у себя
        d = user_data.setdefault(uid, {})
        new_val = not d.get("ach_notify", True)
        d["ach_notify"] = new_val
        save_data()
        await tg_answer(q, "Уведомления об ачивках включены 🔔" if new_val
                        else "Уведомления об ачивках выключены 🔕")
        text, kb = _ach_home_text(uid), _ach_home_kb(uid)
    elif action == "optout":
        # участие/выход из статистики и топа — доступно только себе
        d = user_data.setdefault(uid, {})
        new_val = not d.get("ach_optout", False)
        d["ach_optout"] = new_val
        save_data()
        await tg_answer(q, "Скрыт из топа и чужих просмотров 🙈 (данные и ачивки копятся как раньше)" if new_val
                        else "Снова виден в топе 👁")
        text, kb = _ach_home_text(uid), _ach_home_kb(uid)
    elif action == "cat":
        try:
            idx = int(parts[2])
        except (IndexError, ValueError):
            idx = 0
        idx = max(0, min(idx, len(ACH_CATEGORIES) - 1))
        raw_target = parts[3] if len(parts) > 3 else None
        target, ok = _ach_resolve_target(uid, raw_target)
        await tg_answer(q, None if ok else "Профиль недоступен")
        text, kb = _ach_cat_text(target, idx, viewer_uid=uid), _ach_cat_kb(idx, target, uid)
    elif action == "stats":
        # подробная статистика — обычно только для себя; админу доступна и чужая,
        # в т.ч. скрытых из топа участников
        raw_target = parts[2] if len(parts) > 2 else None
        target, ok = _ach_resolve_target(uid, raw_target)
        if target != uid and uid not in ADMIN_IDS:
            target, ok = uid, False
        await tg_answer(q, None if ok else "Статистика доступна только для себя")
        text, kb = _ach_stats_text(target, viewer_uid=uid), _ach_back_kb(target, uid)
    elif action == "top":
        await tg_answer(q)
        show_hidden = uid in ADMIN_IDS and uid in _admin_top_hidden_view
        full_rec = uid in _top_records_full
        text = _ach_top_text(uid, include_hidden=show_hidden, full_records=full_rec)
        kb = _ach_top_kb(uid, include_hidden=show_hidden, full_records=full_rec)
    elif action == "toptoggle":
        # тумблер "показать скрытых" на экране топа — доступен только админу
        if uid in ADMIN_IDS:
            if uid in _admin_top_hidden_view:
                _admin_top_hidden_view.discard(uid)
            else:
                _admin_top_hidden_view.add(uid)
        await tg_answer(q)
        show_hidden = uid in ADMIN_IDS and uid in _admin_top_hidden_view
        full_rec = uid in _top_records_full
        text = _ach_top_text(uid, include_hidden=show_hidden, full_records=full_rec)
        kb = _ach_top_kb(uid, include_hidden=show_hidden, full_records=full_rec)
    elif action == "recfull":
        # разворот/сворачивание блока "Рекордсмены"
        if uid in _top_records_full:
            _top_records_full.discard(uid)
        else:
            _top_records_full.add(uid)
        await tg_answer(q)
        show_hidden = uid in ADMIN_IDS and uid in _admin_top_hidden_view
        full_rec = uid in _top_records_full
        text = _ach_top_text(uid, include_hidden=show_hidden, full_records=full_rec)
        kb = _ach_top_kb(uid, include_hidden=show_hidden, full_records=full_rec)
    elif action == "home":
        raw_target = parts[2] if len(parts) > 2 else None
        target, ok = _ach_resolve_target(uid, raw_target)
        await tg_answer(q, None if ok else "Профиль недоступен")
        text, kb = _ach_home_text(target, viewer_uid=uid), _ach_home_kb(target, uid)
    else:
        await tg_answer(q)
        text, kb = _ach_home_text(uid), _ach_home_kb(uid)

    return text, kb


async def achievements_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_achievements(update, context)


def _decode_callback_data(data: str) -> str:
    try:
        parts = data.split(":")
        kind = parts[0]
        if kind == "ach":
            return "смотрит достижения"
        if kind == "mv":
            token, action = parts[1], parts[2]
            if action == "adm":
                return f"открыл голос за участника (совпадение {token})"
            val = action[-1]
            phase = "первичная" if action[:-1] == "i" else ("финальная" if action[:-1] == "f" else "")
            val_human = "Робот" if val == "r" else "Человек"
            label = f"{phase} оценка: {val_human}" if phase else val_human
            return f"оценка совпадения {token} — {label}"
        if kind == "mva":
            token, target_uid, action = parts[1], parts[2], parts[3]
            val_human = "Робот" if action.endswith("r") else "Человек"
            target_name = user_data.get(int(target_uid), {}).get("name", target_uid)
            return f"[админ] поставил за {target_name} оценку {val_human} (совпадение {token})"
        if kind == "ph":
            uid = parts[1]
            target_name = user_data.get(int(uid), {}).get("name", uid)
            return f"запросил историю участника {target_name}"
    except Exception:
        pass
    return data


def access_keyboard(uid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Разрешить", callback_data=f"acc:a:{uid}"),
        InlineKeyboardButton("⛔ Отклонить", callback_data=f"acc:d:{uid}"),
    ]])


async def _is_group_member(bot, uid) -> bool:
    if not GROUP_CHAT_ID:
        return False
    try:
        m = await tg(bot.get_chat_member, GROUP_CHAT_ID, uid, _tries=2)
    except Exception as e:
        logger.debug(f"access: get_chat_member {uid}: {e}")
        return False
    return getattr(m, "status", "") in ("creator", "owner", "administrator", "member", "restricted")


async def _notify_admins_request(bot, user) -> bool:
    """Заявка на доступ админам. Повторную не шлём чаще ACCESS_REQUEST_COOLDOWN."""
    uid = user.id
    rec = access_data.get(uid) or {}
    now = time.time()
    try:
        last = float(rec.get("notified_at") or 0)
    except (TypeError, ValueError):
        last = 0.0
    if rec.get("status") == "pending" and (now - last) < ACCESS_REQUEST_COOLDOWN:
        return False
    display_name = getattr(user, "full_name", None) or getattr(user, "first_name", None) or str(uid)
    rec.update({
        "status": "pending",
        "username": user.username,
        "name": display_name,
        "requested_at": datetime.now().isoformat(),
        "notified_at": now,
    })
    access_data[uid] = rec
    save_access()

    href = mention_href(uid, user.username)
    uname = f"@{html.escape(str(user.username))}" if user.username else "—"
    text = (
        "🔐 <b>ЗАПРОС ДОСТУПА К БОТУ</b>\n\n"
        f'👤 <a href="{href}">{html.escape(str(display_name))}</a>\n'
        f"🔗 Username: {uname}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
        "Человека нет в списке участников. Пустить в бота?"
    )
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            await tg(bot.send_message, chat_id=admin_id, text=text, parse_mode="HTML",
                     disable_web_page_preview=True, reply_markup=access_keyboard(uid))
            delivered += 1
        except Exception as e:
            logger.warning(f"access: заявка не доставлена админу {admin_id}: {e}")
    logger.info(f"AUDIT: заявка на доступ от {display_name} ({uid}), доставлено админам: {delivered}")
    return True


async def access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пропускает только одобренных. Остальным — заявка админу и стоп."""
    if not ACCESS_CONTROL:
        return
    user = update.effective_user
    if user is None or getattr(user, "is_bot", False):
        return
    chat = update.effective_chat
    if chat is not None and chat.type != "private":
        return
    uid = user.id
    if is_approved(uid):
        rec = access_data.get(uid)
        if isinstance(rec, dict) and user.username and rec.get("username") != user.username:
            rec["username"] = user.username
            save_access()
        return

    status = access_status(uid)
    if status == "denied":
        logger.info(f"AUDIT: отклонённый {uid} стучится в бота — молчим")
        if update.callback_query:
            await tg_answer(update.callback_query, "⛔ Доступ к боту закрыт.", show_alert=True)
        raise ApplicationHandlerStop

    if AUTO_APPROVE_GROUP_MEMBERS and await _is_group_member(context.bot, uid):
        set_access(uid, "approved", decided_by="auto:group", user=user)
        logger.info(f"access: {uid} состоит в рабочей группе — доступ выдан автоматом")
        return

    was_pending = status == "pending"
    await _notify_admins_request(context.bot, user)
    if update.callback_query:
        await tg_answer(update.callback_query, "⏳ Заявка отправлена админу. Ждём решения.",
                        show_alert=True)
    elif update.message and not was_pending:
        try:
            await tg(update.message.reply_text,
                     "🔐 Бот закрытый, доступ только по одобрению.\n\n"
                     "⏳ Заявка ушла администратору. Как одобрит — придёт уведомление.",
                     reply_markup=ReplyKeyboardRemove(), _tries=2)
        except Exception as e:
            logger.warning(f"access: не смог ответить {uid}: {e}")
    raise ApplicationHandlerStop


async def access_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q is None or update.effective_user is None:
        return
    admin_id = update.effective_user.id
    if admin_id not in ADMIN_IDS:
        await tg_answer(q, "⛔ Решение принимает только админ.", show_alert=True)
        return
    try:
        _, action, raw_uid = (q.data or "").split(":")
        target = int(raw_uid)
    except Exception:
        await tg_answer(q, "❌ Битые данные кнопки.", show_alert=True)
        return
    if action not in ("a", "d"):
        await tg_answer(q, "❌ Неизвестное действие.", show_alert=True)
        return

    approve = action == "a"
    prev = access_status(target)
    set_access(target, "approved" if approve else "denied", decided_by=admin_id)
    await tg_answer(q, "✅ Доступ выдан" if approve else "⛔ Отклонён")

    verdict = "✅ <b>ДОСТУП РАЗРЕШЁН</b>" if approve else "⛔ <b>ДОСТУП ОТКЛОНЁН</b>"
    stamp = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    base = ""
    try:
        base = q.message.text_html if q.message is not None else ""
    except Exception:
        base = ""
    try:
        await tg(q.edit_message_text, f"{base}\n\n{verdict}\n🕒 {stamp}",
                 parse_mode="HTML", disable_web_page_preview=True, _tries=2)
    except BadRequest:
        pass
    except Exception as e:
        logger.warning(f"access: карточку заявки обновить не вышло: {e}")

    if approve and prev != "approved":
        try:
            await tg(context.bot.send_message, chat_id=target,
                     text="✅ Админ открыл тебе доступ к боту.\n\nЖми /start — начнём.",
                     _tries=2)
        except Exception as e:
            logger.warning(f"access: не смог уведомить {target} об одобрении: {e}")
    logger.info(f"AUDIT: админ {admin_id} "
                f"{'разрешил' if approve else 'отклонил'} доступ пользователю {target}")


async def access_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await tg(update.message.reply_text, "⛔ У вас нет прав на эту команду.")
        return
    pending = access_users("pending")
    approved = access_users("approved")
    denied = access_users("denied")
    lines = ["🔐 <b>ДОСТУП К БОТУ</b>", "",
             f"✅ Разрешено: {len(approved)}",
             f"⏳ Ждут решения: {len(pending)}",
             f"⛔ Отклонено: {len(denied)}", ""]
    if approved:
        lines.append("✅ <b>Разрешённые:</b>")
        lines += [f"• {_access_display(u)}" for u in approved]
        lines.append("")
    if denied:
        lines.append("⛔ <b>Отклонённые:</b>")
        lines += [f"• {_access_display(u)}" for u in denied]
        lines.append("")
    lines.append("Команды: /approve &lt;id&gt; — выдать, /deny &lt;id&gt; — забрать доступ")
    await send_blocks(context.bot, update.effective_chat.id, ["\n".join(lines)])
    for u in pending:
        try:
            await tg(context.bot.send_message, chat_id=uid,
                     text=f"⏳ <b>Ждёт доступа</b>\n{_access_display(u)}",
                     parse_mode="HTML", disable_web_page_preview=True,
                     reply_markup=access_keyboard(u))
        except Exception as e:
            logger.warning(f"access: не показал заявку {u}: {e}")


async def _access_manual(update: Update, context: ContextTypes.DEFAULT_TYPE, approve: bool):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await tg(update.message.reply_text, "⛔ У вас нет прав на эту команду.")
        return
    args = context.args or []
    if len(args) != 1 or not args[0].lstrip("-").isdigit():
        await tg(update.message.reply_text,
                 f"Формат: /{'approve' if approve else 'deny'} 123456789")
        return
    target = int(args[0])
    if not approve and target in ADMIN_IDS:
        await tg(update.message.reply_text, "⛔ Админа отключить нельзя.")
        return
    prev = access_status(target)
    set_access(target, "approved" if approve else "denied", decided_by=uid)
    await tg(update.message.reply_text,
             ("✅ Доступ выдан: " if approve else "⛔ Доступ забран: ") + _access_display(target),
             parse_mode="HTML", disable_web_page_preview=True)
    if approve and prev != "approved":
        try:
            await tg(context.bot.send_message, chat_id=target,
                     text="✅ Админ открыл тебе доступ к боту.\n\nЖми /start — начнём.", _tries=2)
        except Exception as e:
            logger.warning(f"access: не смог уведомить {target}: {e}")
    logger.info(f"AUDIT: админ {uid} вручную "
                f"{'разрешил' if approve else 'отклонил'} доступ {target}")


async def access_approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _access_manual(update, context, True)


async def access_deny_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _access_manual(update, context, False)


async def audit_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if u is None:
        return
    who = user_data.get(u.id, {}).get("name") or str(u.id)
    if update.callback_query:
        logger.info(f"AUDIT: {who} нажал кнопку: {_decode_callback_data(update.callback_query.data)}")
    elif update.message and update.message.text:
        logger.info(f"AUDIT: {who} написал текст={update.message.text!r}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        logger.warning(f"Сеть: {type(err).__name__}: {err}")
        return
    logger.error(f"Ошибка: {err}")
    try:
        if update and update.effective_message:
            await tg(update.effective_message.reply_text,
                     "⚠️ Произошла техническая ошибка. Администраторы уведомлены.",
                     _tries=2)
    except Exception:
        pass


async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args or []

    def parse_offset(s):
        try:
            v = int(s)
        except ValueError:
            return None
        return v if -12 <= v <= 14 else None

    if len(args) == 2 and user_id in ADMIN_IDS:
        try:
            target_uid = int(args[0])
        except ValueError:
            await tg(update.message.reply_text, "❌ Формат: /tz <telegram_id> <±N>")
            return
        offset = parse_offset(args[1])
        if offset is None:
            await tg(update.message.reply_text, "❌ Сдвиг — целое число от -12 до 14, напр. /tz 123456 +2")
            return
        if target_uid not in user_data or not isinstance(user_data[target_uid], dict):
            await tg(update.message.reply_text, "❌ Такого участника нет.")
            return
        user_data[target_uid]["utc_offset"] = offset
        save_data()
        name = user_data[target_uid].get("name", target_uid)
        await tg(update.message.reply_text, f"✅ Часовой пояс {name}: МСК{offset:+d}")
        return

    if len(args) == 1:
        offset = parse_offset(args[0])
        if offset is None:
            await tg(update.message.reply_text, "❌ Сдвиг — целое число от -12 до 14, напр. /tz +2")
            return
        if user_id not in user_data or not isinstance(user_data[user_id], dict):
            await tg(update.message.reply_text, "❌ Ты ещё не зарегистрирован! Нажми /start")
            return
        user_data[user_id]["utc_offset"] = offset
        save_data()
        await tg(update.message.reply_text,
            f"✅ Записал: твоё время = МСК{offset:+d}. "
            f"Теперь время сессий в «Актуальных» и карточках будет по твоим часам.")
        return

    current = _user_utc_offset(user_id)
    await tg(update.message.reply_text,
        f"🕐 Сейчас твой сдвиг: МСК{current:+d}.\n"
        "Изменить: /tz +2 (если твоё время на 2 часа впереди московского), /tz -1 и т.п.\n"
        "Если совпадает с Москвой — можно не трогать.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await tg(update.message.reply_text,
        "❌ Действие отменено.",
        reply_markup=get_main_keyboard(user_id) if is_user_active(user_id) else get_start_keyboard()
    )
    return ConversationHandler.END


def _parse_added_at(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _user_utc_offset(uid) -> int:
    data = user_data.get(uid, {}) or {}
    try:
        return int(data.get("utc_offset", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _fmt_local(added_at_str, uid, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    dt = _parse_added_at(added_at_str)
    if not dt:
        return added_at_str
    offset = _user_utc_offset(uid)
    local_dt = dt + timedelta(hours=offset) if offset else dt
    return local_dt.strftime(fmt)


def _fmt_local_dt(dt, uid, fmt: str = "%d.%m.%Y %H:%M:%S") -> str:
    if not dt:
        return dt
    offset = _user_utc_offset(uid)
    local_dt = dt + timedelta(hours=offset) if offset else dt
    return local_dt.strftime(fmt)


def _last_submission_dt(uid, data):
    best = None

    def consider(s, iso=False):
        nonlocal best
        if not s:
            return
        dt = datetime.fromisoformat(s) if iso else _parse_added_at(s)
        if dt and (best is None or dt > best):
            best = dt

    links = data.get("links", {}) or {}
    for ld in links.values():
        if isinstance(ld, dict):
            consider(ld.get("added_at", ""))

    sub = data.get("last_submission") or {}
    consider(sub.get("at", ""))

    for h in user_links_history.get(uid, []):
        if isinstance(h, dict):
            consider(h.get("added_at", ""))

    return best


def cleanup_old_links():
    global user_links_history, reported_matches
    now = datetime.now()
    cutoff = now - timedelta(days=LINKS_TTL_DAYS)
    removed = 0

    for uid in list(user_links_history.keys()):
        entries = user_links_history.get(uid)
        if not isinstance(entries, list):
            logger.warning(f"История ссылок {uid} не список — сбрасываю запись")
            user_links_history.pop(uid, None)
            removed += 1
            continue
        kept = []
        for entry in entries:
            if not isinstance(entry, dict):
                removed += 1
                continue
            dt = _parse_added_at(entry.get("added_at", ""))
            if dt is None or dt >= cutoff:
                kept.append(entry)
            else:
                removed += 1
        if kept:
            user_links_history[uid] = kept
        else:
            user_links_history.pop(uid)

    if removed:
        save_user_links_history(user_links_history)

    alive = {e.get("url") for lst in user_links_history.values()
             if isinstance(lst, list) for e in lst if isinstance(e, dict)}
    stale = [link for link in reported_matches if link not in alive]
    for link in stale:
        reported_matches.pop(link)
    if stale:
        save_reported_matches(reported_matches)

    global match_votes
    stale_votes = [t for t, st in match_votes.items()
                   if not isinstance(st, dict) or st.get("link") not in alive]
    for t in stale_votes:
        match_votes.pop(t)
    if stale_votes:
        save_match_votes()

    global link_verdicts
    stale_verdicts = [link for link in link_verdicts if link not in alive]
    for link in stale_verdicts:
        link_verdicts.pop(link)
    if stale_verdicts:
        save_link_verdicts()

    logger.info(
        f"Очистка TTL={LINKS_TTL_DAYS}д: удалено ссылок {removed}, "
        f"пометок совпадений {len(stale)}. Граница: {cutoff.strftime('%Y-%m-%d %H:%M:%S')}."
    )
    return {
        "links": removed,
        "reported": len(stale),
        "votes": len(stale_votes),
        "verdicts": len(stale_verdicts),
        "cutoff": cutoff.strftime('%Y-%m-%d %H:%M:%S'),
    }


def cleanup_stale_files():
    data_files = [DATA_FILE, USER_LINKS_HISTORY_FILE, REPORTED_MATCHES_FILE,
                  MATCH_VOTES_FILE, LINK_VERDICTS_FILE, ACHIEVEMENTS_FILE]
    removed_tmp = 0
    for path in data_files + [SESSION_STATE_FILE]:
        for tmp in glob.glob(f"{path}.tmp*"):
            try:
                os.remove(tmp)
                removed_tmp += 1
            except Exception as e:
                logger.warning(f"Не удалось удалить .tmp-хвост {tmp}: {e}")

    removed_bak = 0
    for path in data_files + [SESSION_STATE_FILE]:
        # .backup — единственный живой зеркальный файл, его не трогаем.
        # Накапливаются только .corrupt_<stamp> (и legacy .backup_<stamp>).
        backups = glob.glob(f"{path}.backup_*") + glob.glob(f"{path}.corrupt_*")
        if len(backups) <= BACKUP_KEEP:
            continue
        backups.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in backups[BACKUP_KEEP:]:
            try:
                os.remove(old)
                removed_bak += 1
            except Exception as e:
                logger.warning(f"Не удалось удалить старый бэкап {old}: {e}")

    if removed_tmp or removed_bak:
        logger.info(
            f"Подметено .tmp-хвостов {removed_tmp}, "
            f"старых бэкапов {removed_bak} (оставлено по {BACKUP_KEEP} свежих на файл)."
        )
    return {"tmp": removed_tmp, "backups": removed_bak}


async def auto_cleanup_loop():
    """Фоновая автоочистка TTL. Работает всё время, пока живёт процесс бота.
    Перезапуск бота и команда /cleanup для этого не нужны."""
    interval = max(300, int(CLEANUP_INTERVAL_HOURS * 3600))
    logger.info(f"🧽 Автоочистка запущена: каждые {CLEANUP_INTERVAL_HOURS} ч, TTL={LINKS_TTL_DAYS} дн.")
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("🧽 Автоочистка остановлена.")
            raise
        try:
            # синхронно, без await внутри: не пересекается с хендлерами,
            # которые правят те же словари
            st = cleanup_old_links()
            files = cleanup_stale_files()
            logger.info(
                f"🧽 Автоочистка: ссылок {st['links']}, вердиктов {st['verdicts']}, "
                f"голосований {st['votes']}, пометок {st['reported']}, "
                f".tmp {files['tmp']}, бэкапов {files['backups']}. Граница: {st['cutoff']}."
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"🧽 Автоочистка: ошибка цикла (продолжаю работать): {e}")


def reset_for_new_run():
    global last_check_time
    last_check_time = {}
    changed = False
    kept_links = 0
    kept_subs = 0
    for d in user_data.values():
        if not isinstance(d, dict):
            continue
        if d.pop("last_check_snapshot", None) is not None:
            changed = True
        if _migrate_submission_stack(d):
            changed = True
        if d.get("links"):
            kept_links += 1
        if d.get("submissions"):
            kept_subs += 1
    _migrate_reported_matches()
    if changed:
        save_data()
    logger.info(
        f"Старт: текущие сессии сохранены у {kept_links} чел., "
        f"отменяемых отправок у {kept_subs} чел. — бот готов к работе."
    )


_bot_loop = None
_bot_app = None


def _load_session_active() -> bool:
    try:
        if os.path.exists(SESSION_STATE_FILE):
            with open(SESSION_STATE_FILE, 'r', encoding='utf-8') as f:
                return bool(json.load(f).get("active", False))
    except Exception as e:
        logger.warning(f"session_state: не удалось прочитать, считаю False ({e})")
    return False


def _save_session_active(value: bool):
    try:
        _atomic_write_json(SESSION_STATE_FILE, {"active": value})
    except Exception as e:
        logger.warning(f"session_state: не удалось сохранить ({e})")


BROADCAST_DEDUP_SECONDS = 120
_last_broadcast = {}


def _broadcast_is_duplicate(kind: str, payload) -> bool:
    try:
        sig = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        sig = str(payload)
    now = time.monotonic()
    prev = _last_broadcast.get(kind)
    if prev and prev[0] == sig and (now - prev[1]) < BROADCAST_DEDUP_SECONDS:
        return True
    _last_broadcast[kind] = (sig, now)
    return False


_search_session_active = _load_session_active()


async def broadcast_search_session(payload: dict):
    global _search_session_active
    if _bot_app is None:
        logger.warning("broadcast: приложение бота ещё не готово")
        return 0
    payload_sig = {k: payload.get(k) for k in ("price", "time", "poolTime")}
    if _broadcast_is_duplicate("session", payload_sig):
        logger.info("broadcast: пропущен дубль события (уже рассылали недавно)")
        return 0
    bot = _bot_app.bot

    _search_session_active = True
    _save_session_active(True)

    def esc(v):
        return html.escape(str(v if v is not None else ""))

    lines = [
        "🔔 <b>Разметка поисковых сессий</b>",
        f"<b>Цена:</b> {esc(payload.get('price'))}",
        f"<b>Время:</b> {esc(payload.get('time'))}",
        f"<b>Время пула:</b> {esc(payload.get('poolTime'))}",
    ]
    text = "\n".join(lines)

    sent, failed = 0, 0
    for uid, data in list(user_data.items()):
        if not isinstance(data, dict) or not data.get("registered"):
            continue
        if not is_approved(uid):
            continue
        if not data.get("notify", True):
            continue
        try:
            await tg(bot.send_message,
                chat_id=uid, text=text,
                parse_mode="HTML", disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"broadcast: не доставлено {uid}: {e}")
    logger.info(f"📣 'Разметка поисковых сессий' разослана: {sent} ок, {failed} ошибок")
    return sent


async def broadcast_session_ended():
    global _search_session_active
    if not _search_session_active:
        logger.info("session_ended: пропущено (активной сессии не было)")
        return 0
    if _bot_app is None:
        logger.warning("session_ended: приложение бота ещё не готово")
        return 0
    if _broadcast_is_duplicate("ended", "ended"):
        logger.info("session_ended: пропущен дубль события")
        return 0
    bot = _bot_app.bot

    _search_session_active = False
    _save_session_active(False)

    text = (
        "✅ <b>Разметка поисковых сессий</b> — сессии закончились.\n"
        "На рабочем столе больше нет заданий."
    )

    sent, failed = 0, 0
    for uid, data in list(user_data.items()):
        if not isinstance(data, dict) or not data.get("registered"):
            continue
        if not is_approved(uid):
            continue
        if not data.get("notify", True):
            continue
        try:
            await tg(bot.send_message,
                chat_id=uid, text=text,
                parse_mode="HTML", disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"session_ended: не доставлено {uid}: {e}")
    logger.info(f"📣 «Сессии закончились» разослано: {sent} ок, {failed} ошибок")
    return sent


async def broadcast_session_price_changed(payload: dict):
    if _bot_app is None:
        logger.warning("price_changed: приложение бота ещё не готово")
        return 0
    if not _search_session_active:
        logger.info("price_changed: пропущено (активной сессии не было)")
        return 0
    if _broadcast_is_duplicate("price", {k: payload.get(k)
                                         for k in ("endedPrices", "currentPrices")}):
        logger.info("price_changed: пропущен дубль события")
        return 0
    bot = _bot_app.bot

    def esc(v):
        return html.escape(str(v if v is not None else ""))

    ended = esc(payload.get("endedPrices"))
    current = esc(payload.get("currentPrices"))
    lines = ["🔁 <b>Разметка поисковых сессий</b> — цена изменилась."]
    if ended:
        lines.append(f"Закончились за <b>{ended}</b>.")
    if current:
        lines.append(f"Сейчас на столе за <b>{current}</b>.")
    text = "\n".join(lines)

    sent, failed = 0, 0
    for uid, data in list(user_data.items()):
        if not isinstance(data, dict) or not data.get("registered"):
            continue
        if not is_approved(uid):
            continue
        if not data.get("notify", True):
            continue
        try:
            await tg(bot.send_message,
                chat_id=uid, text=text,
                parse_mode="HTML", disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"price_changed: не доставлено {uid}: {e}")
    logger.info(f"📣 «Цена изменилась» разослано: {sent} ок, {failed} ошибок")
    return sent


class _NotifyHandler(BaseHTTPRequestHandler):

    def _json(self, code, msg):
        body = json.dumps({"ok": code == 200, "msg": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip("/") != "/notify":
            self._json(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self._json(400, f"bad json: {e}")
            return

        if data.get("secret") != NOTIFY_SECRET:
            self._json(403, "forbidden")
            return

        if str(data.get("event")) == "ended":
            if _bot_loop is None:
                self._json(503, "bot not ready")
                return
            try:
                asyncio.run_coroutine_threadsafe(broadcast_session_ended(), _bot_loop)
                self._json(200, "queued: ended")
            except Exception as e:
                self._json(500, f"error: {e}")
            return

        if str(data.get("event")) == "price_changed":
            if _bot_loop is None:
                self._json(503, "bot not ready")
                return
            try:
                asyncio.run_coroutine_threadsafe(broadcast_session_price_changed(data), _bot_loop)
                self._json(200, "queued: price_changed")
            except Exception as e:
                self._json(500, f"error: {e}")
            return

        if str(data.get("hash")) != SEARCH_SESSION_HASH:
            self._json(200, "ignored: wrong hash")
            return
        if _bot_loop is None:
            self._json(503, "bot not ready")
            return
        try:
            asyncio.run_coroutine_threadsafe(broadcast_search_session(data), _bot_loop)
            self._json(200, "queued")
        except Exception as e:
            self._json(500, f"error: {e}")

    def log_message(self, *args, **kwargs):
        pass


NOTIFY_BIND_RETRY_SECONDS = 5


def _start_notify_server():
    warned = False
    while True:
        try:
            srv = HTTPServer((NOTIFY_HTTP_HOST, NOTIFY_HTTP_PORT), _NotifyHandler)
        except Exception as e:
            if not warned:
                logger.error(
                    f"❌ Порт {NOTIFY_HTTP_HOST}:{NOTIFY_HTTP_PORT} занят ({e}). "
                    f"Похоже, старый процесс бота ещё жив. "
                    f"Повторяю каждые {NOTIFY_BIND_RETRY_SECONDS}с…")
                warned = True
            time.sleep(NOTIFY_BIND_RETRY_SECONDS)
            continue
        logger.info(f"🌐 Приёмник уведомлений: http://{NOTIFY_HTTP_HOST}:{NOTIFY_HTTP_PORT}/notify")
        warned = False
        try:
            srv.serve_forever()
        except Exception as e:
            logger.error(f"Приёмник уведомлений упал ({e}), поднимаю заново")
        finally:
            try:
                srv.server_close()
            except Exception:
                pass
        time.sleep(1)


async def _backfill_usernames(app):
    bot = app.bot
    targets = [
        uid for uid, d in list(user_data.items())
        if isinstance(d, dict) and d.get("registered") and not d.get("username")
    ]
    if not targets:
        return
    logger.info(f"🔎 Бэкфилл username: проверяю {len(targets)} участников…")
    updated = 0
    for uid in targets:
        try:
            chat = await tg(bot.get_chat, uid)
            if chat.username and user_data.get(uid, {}).get("username") != chat.username:
                user_data[uid]["username"] = chat.username
                updated += 1
        except Exception as e:
            logger.debug(f"backfill username {uid}: пропуск ({e})")
        await asyncio.sleep(0.3)
    if updated:
        save_data()
    logger.info(f"🔎 Бэкфилл username завершён: обновлено {updated}")


async def _on_notify_post_init(app):
    global _bot_loop, _bot_app
    _bot_app = app
    _bot_loop = asyncio.get_running_loop()
    threading.Thread(target=_start_notify_server, daemon=True).start()
    app.bot_data.setdefault("_bg_tasks", set())
    for coro in (_backfill_usernames(app), _send_bot_ready_broadcast(app.bot), auto_cleanup_loop()):
        task = asyncio.create_task(coro)
        app.bot_data["_bg_tasks"].add(task)
        task.add_done_callback(app.bot_data["_bg_tasks"].discard)


def main():
    try:
        cleanup_stale_files()
        cleanup_old_links()
        reset_for_new_run()

        builder = Application.builder().token(BOT_TOKEN)
        if hasattr(builder, "media_write_timeout"):
            builder = builder.media_write_timeout(NET_WRITE_TIMEOUT)
        application = (
            builder
            .connect_timeout(NET_CONNECT_TIMEOUT)
            .read_timeout(NET_READ_TIMEOUT)
            .write_timeout(NET_WRITE_TIMEOUT)
            .pool_timeout(NET_POOL_TIMEOUT)
            .connection_pool_size(NET_POOL_SIZE)
            .get_updates_connect_timeout(NET_CONNECT_TIMEOUT)
            .get_updates_read_timeout(NET_GETUPDATES_READ_TIMEOUT)
            .get_updates_write_timeout(NET_WRITE_TIMEOUT)
            .get_updates_pool_timeout(NET_POOL_TIMEOUT)
            .post_init(_on_notify_post_init)
            .build()
        )
        PRIVATE = filters.ChatType.PRIVATE

        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("start", start, filters=PRIVATE),
                CommandHandler("change_name", change_name_start, filters=PRIVATE),
                MessageHandler(PRIVATE & filters.Regex("^🚀 Начать работу$"), start_work),
                MessageHandler(PRIVATE & filters.Regex("^🔄 Новая сессия$"), new_session),
                MessageHandler(PRIVATE & filters.Regex("^✏️ Сменить имя$"), change_name_start),
            ],
            states={
                WAITING_FOR_NAME: [MessageHandler(PRIVATE & filters.TEXT & ~filters.COMMAND, get_name)],
                WAITING_FOR_LINKS: [MessageHandler(PRIVATE & filters.TEXT & ~filters.COMMAND, process_links)],
                WAITING_FOR_NEW_NAME: [MessageHandler(PRIVATE & filters.TEXT & ~filters.COMMAND, change_name_save)],
            },
            fallbacks=[
                CommandHandler("cancel", cancel, filters=PRIVATE),
                MessageHandler(PRIVATE & filters.Regex("^❌ Отмена$"), cancel),
            ],
            allow_reentry=True,
        )

        application.add_handler(MessageHandler(filters.ALL, access_gate), group=-2)
        application.add_handler(CallbackQueryHandler(access_gate), group=-2)
        application.add_handler(MessageHandler(filters.ALL, audit_log), group=-1)
        application.add_handler(CallbackQueryHandler(audit_log), group=-1)

        application.add_handler(conv_handler)
        application.add_handler(CallbackQueryHandler(vote_callback, pattern=r"^mv:"))
        application.add_handler(CallbackQueryHandler(proxy_vote_callback, pattern=r"^mva:"))
        application.add_handler(CallbackQueryHandler(participant_history_callback, pattern=r"^ph:"))
        application.add_handler(CallbackQueryHandler(achievements_callback, pattern=r"^ach:"))
        application.add_handler(CallbackQueryHandler(full_reset_confirm, pattern=r"^fr:"))
        application.add_handler(CallbackQueryHandler(access_decision_callback, pattern=r"^acc:"))
        application.add_handler(CommandHandler("access", access_list_cmd, filters=PRIVATE))
        application.add_handler(CommandHandler("approve", access_approve_cmd, filters=PRIVATE))
        application.add_handler(CommandHandler("deny", access_deny_cmd, filters=PRIVATE))
        application.add_handler(CommandHandler("full_reset", full_reset, filters=PRIVATE))
        application.add_handler(CommandHandler("cleanup", cleanup_now, filters=PRIVATE))
        application.add_handler(CommandHandler("undo", undo_last_submission, filters=PRIVATE))
        application.add_handler(CommandHandler("tz", set_timezone, filters=PRIVATE))
        application.add_handler(CommandHandler("myid", my_id, filters=PRIVATE))
        application.add_handler(CommandHandler("ach", achievements_cmd, filters=PRIVATE))
        application.add_handler(CommandHandler("skill_on", skill_on_cmd, filters=PRIVATE))
        application.add_handler(CommandHandler("skill_off", skill_off_cmd, filters=PRIVATE))
        application.add_handler(CommandHandler("auto", auto_check_cmd, filters=PRIVATE))
        application.add_handler(MessageHandler(PRIVATE & filters.TEXT & ~filters.COMMAND, handle_free_text))
        application.add_error_handler(error_handler)

        print("🤖 Бот запущен и готов к работе!")
        print(f"📁 Данные: {DATA_FILE}")
        print(f"👥 Группа: {GROUP_CHAT_ID}")
        print(f"👑 Администраторы: {ADMIN_IDS}")
        print(f"🔐 Доступ по одобрению: {'вкл' if ACCESS_CONTROL else 'выкл'} (разрешено: {len(access_users('approved'))}, ждут: {len(access_users('pending'))})")
        print(f"📣 Право на рассылку: {ADMIN_IDS + BROADCAST_IDS}")
        print(f"🧽 Автоочистка: каждые {CLEANUP_INTERVAL_HOURS} ч (TTL {LINKS_TTL_DAYS} дн.)")
        print(f"🏆 Достижения: {len(ACHIEVEMENTS_DEF)} шт., {sum(len(a['tiers']) for a in ACHIEVEMENTS_DEF)} тиров")
        print("✏️ Сменить имя: /change_name")

        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            timeout=NET_LONGPOLL_TIMEOUT,
            drop_pending_updates=False,
        )
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}")
        print(f"❌ Ошибка запуска: {e}")


if __name__ == "__main__":
    main()