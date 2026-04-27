import logging
import asyncio
import re
import io
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import BOT_TOKEN, ADMIN_ID, ADMIN_USERNAME, ADMIN_CONTACT, TRIAL_DAYS, SUBSCRIPTION_PRICE, ACTIVITY_LEVELS
from food_search import FoodSearch
from db import UserDB
from export import export_users_to_excel

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
food_search = FoodSearch()
user_db = UserDB()

# ============ СОСТОЯНИЯ ============

class ProfileState(StatesGroup):
    waiting_for_name = State()
    waiting_for_age = State()
    waiting_for_weight = State()
    waiting_for_height = State()
    waiting_for_gender = State()
    waiting_for_activity = State()

class WaitingState(StatesGroup):
    waiting_for_correction = State()

class AdminState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_days = State()
    waiting_for_days_value = State()
    waiting_for_broadcast = State()

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def format_daily_stats(stats: dict, tdee: float = None) -> str:
    text = f"""
Статистика за сегодня:
Калории: {stats['calories']:.0f} ккал
Белки: {stats['protein']:.1f} г
Жиры: {stats['fat']:.1f} г
Углеводы: {stats['carbs']:.1f} г
"""
    if tdee and tdee > 0:
        percent = (stats['calories'] / tdee) * 100
        text += f"\nОт суточной нормы: {percent:.0f}% (норма: {tdee:.0f} ккал)"
    
    return text

def format_subscription_status(subscription: dict) -> str:
    if subscription.get("is_forever"):
        return "Активна (бессрочно)"
    days = subscription.get("days_left", 0)
    if days > 0:
        return f"Активна. Осталось дней: {days}"
    else:
        return f"Истекла. Для продления свяжитесь с админом: {ADMIN_CONTACT}"

async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="stats", description="Статистика за сегодня"),
        BotCommand(command="history", description="История записей"),
        BotCommand(command="clear", description="Очистить статистику"),
        BotCommand(command="profile", description="Мой профиль"),
        BotCommand(command="profile_edit", description="Изменить профиль"),
        BotCommand(command="subscription", description="Статус подписки"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="admin_panel", description="Админ-панель"),
    ]
    await bot.set_my_commands(commands)

async def notify_admin(user_id: int, username: str, first_name: str):
    if ADMIN_ID:
        msg = f"Новый пользователь!\n\nID: {user_id}\nИмя: {first_name}"
        if username:
            msg += f"\nUsername: @{username}"
        await bot.send_message(ADMIN_ID, msg)

def is_admin(user_id: int, username: str = None) -> bool:
    if ADMIN_ID and user_id == ADMIN_ID:
        return True
    if ADMIN_USERNAME and username and username.lower() == ADMIN_USERNAME.lower():
        return True
    return False

def get_gender_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мужской", callback_data="gender_male"),
         InlineKeyboardButton(text="Женский", callback_data="gender_female")]
    ])
    return keyboard

def get_activity_keyboard():
    buttons = []
    for key, value in ACTIVITY_LEVELS.items():
        buttons.append([InlineKeyboardButton(text=value["name"], callback_data=f"activity_{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes"),
            InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no")
        ]
    ])

def extract_product_data(product: dict) -> dict:
    return {
        "name": product.get("name", "Неизвестный продукт"),
        "weight_grams": product.get("weight_grams", 100),
        "calories": product.get("calories", 0),
        "protein": product.get("protein", 0),
        "fat": product.get("fat", 0),
        "carbs": product.get("carbs", 0)
    }

def is_affirmative(text: str) -> bool:
    text = text.lower().strip()
    affirmative = ["да", "yes", "+", "ок", "окей", "хорошо", "верно", "ага", "дада", "записывай"]
    return any(word in text for word in affirmative)

def is_negative(text: str) -> bool:
    text = text.lower().strip()
    negative = ["нет", "no", "-", "не", "неверно", "не правильно", "не так"]
    return any(word in text for word in negative)

def is_correction(text: str) -> bool:
    has_numbers = bool(re.search(r'\d+', text))
    has_units = bool(re.search(r'г|гр|грамм|шт|штук|ложк|стакан|чашка|мл', text.lower()))
    return has_numbers or has_units

def is_delete_command(text: str) -> bool:
    text = text.lower().strip()
    delete_words = ["удали", "убрать", "удалить", "убри", "убери", "delete", "remove"]
    return any(word in text for word in delete_words)

def has_profile(user_id: int) -> bool:
    return user_db.get_profile(user_id) is not None

async def get_user_id_or_username(user_input: str) -> int:
    user_input = user_input.strip()
    if user_input.isdigit():
        return int(user_input)
    else:
        return user_db.get_user_id_by_username(user_input)

def format_date(date_str: str) -> str:
    """Преобразует YYYY-MM-DD в DD.MM.YYYY"""
    if not date_str:
        return date_str
    parts = date_str[:10].split('-')
    if len(parts) == 3:
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return date_str[:10]

# ============ АДМИН-ПАНЕЛЬ ============

@dp.message(Command("admin_panel", "adminpanel"))
async def cmd_admin_panel(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    admin_text = (
        "Админ-панель\n\n"
        "/admin_users — список пользователей\n"
        "/admin_export — выгрузить список в Excel 📊\n"
        "/admin_info user_id или @username — информация о пользователе\n"
        "/admin_add_user — добавить пользователя\n"
        "/admin_extend user_id или @username days — продлить подписку\n"
        "/admin_remove_user user_id или @username — удалить пользователя\n"
        "/admin_activate user_id или @username [days] — активация подписки\n\n"
        "Рассылка:\n"
        "/broadcast_all текст — рассылка всем пользователям\n"
        "/broadcast_active текст — рассылка только активным\n"
        "(или ответьте на сообщение командой /broadcast_all)\n\n"
        "Бэкап:\n"
        "/backup — скачать базу данных users.db\n\n"
        "Реферальные команды:\n"
        "/ref @username процент месяцы — создать реферальную ссылку\n"
        "/ref_stats — статистика по рефералам\n"
        "/ref_link_info код — информация о ссылке"
    )
    
    await message.answer(admin_text)

@dp.message(Command("admin_export"))
async def cmd_admin_export(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    users = user_db.get_all_users()
    if not users:
        await message.answer("Нет пользователей для экспорта")
        return
    
    try:
        excel_data = export_users_to_excel(users)
        
        filename = f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        await message.answer_document(
            document=types.BufferedInputFile(excel_data, filename=filename),
            caption=f"📊 Экспорт пользователей ({len(users)} записей)"
        )
    except Exception as e:
        await message.answer(f"Ошибка при создании Excel-файла: {e}")

# ============ БЭКАП ============

@dp.message(Command("backup"))
async def cmd_backup(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    try:
        from config import USER_DB_PATH
        
        with open(USER_DB_PATH, 'rb') as f:
            file_data = f.read()
        
        await message.answer_document(
            document=types.BufferedInputFile(file_data, filename="users.db"),
            caption="Бэкап базы данных users.db"
        )
        
    except Exception as e:
        await message.answer(f"Ошибка при создании бэкапа: {e}")

# ============ РАССЫЛКА ============

@dp.message(Command("broadcast_all"))
async def cmd_broadcast_all(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    # Получаем текст после команды или из reply-to сообщения
    text = message.text.replace("/broadcast_all", "").strip()
    
    # Если текст не в команде, проверяем reply
    if not text and message.reply_to_message and message.reply_to_message.text:
        text = message.reply_to_message.text.strip()
    
    if not text:
        await message.answer(
            "Использование:\n"
            "/broadcast_all текст сообщения\n"
            "или ответьте на сообщение командой /broadcast_all"
        )
        return
    
    users = user_db.get_all_user_ids()
    await state.update_data(broadcast_text=text, broadcast_users=users, broadcast_type="всем")
    
    await message.answer(
        f"Подтвердите рассылку.\n\n"
        f"Получателей: {len(users)}\n"
        f"Текст: {text[:200]}{'...' if len(text) > 200 else ''}\n\n"
        f"Отправьте ДА для подтверждения или НЕТ для отмены"
    )
    await state.set_state(AdminState.waiting_for_broadcast)

@dp.message(Command("broadcast_active"))
async def cmd_broadcast_active(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    # Получаем текст после команды или из reply-to сообщения
    text = message.text.replace("/broadcast_active", "").strip()
    
    # Если текст не в команде, проверяем reply
    if not text and message.reply_to_message and message.reply_to_message.text:
        text = message.reply_to_message.text.strip()
    
    if not text:
        await message.answer(
            "Использование:\n"
            "/broadcast_active текст сообщения\n"
            "или ответьте на сообщение командой /broadcast_active"
        )
        return
    
    users = user_db.get_active_user_ids()
    await state.update_data(broadcast_text=text, broadcast_users=users, broadcast_type="активным")
    
    await message.answer(
        f"Подтвердите рассылку.\n\n"
        f"Получателей: {len(users)}\n"
        f"Текст: {text[:200]}{'...' if len(text) > 200 else ''}\n\n"
        f"Отправьте ДА для подтверждения или НЕТ для отмены"
    )
    await state.set_state(AdminState.waiting_for_broadcast)

@dp.message(AdminState.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        await state.clear()
        return
    
    if is_affirmative(message.text.lower()):
        data = await state.get_data()
        text = data.get("broadcast_text")
        users = data.get("broadcast_users")
        broadcast_type = data.get("broadcast_type")
        
        await message.answer(f"Начинаю рассылку для {len(users)} пользователей...")
        
        success = 0
        failed = 0
        
        for user_id in users:
            try:
                await bot.send_message(user_id, text)
                success += 1
            except Exception as e:
                failed += 1
                logging.error(f"Ошибка отправки {user_id}: {e}")
            
            await asyncio.sleep(0.05)
        
        await message.answer(
            f"Рассылка завершена.\n"
            f"Успешно: {success}\n"
            f"Ошибок: {failed}"
        )
        await state.clear()
    else:
        await message.answer("Рассылка отменена")
        await state.clear()

# ============ АДМИНСКИЕ КОМАНДЫ ============

@dp.message(Command("admin_add_user"))
async def cmd_admin_add_user(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    await message.answer("Введите ID пользователя или @username (можно получить из /admin_users)")
    await state.set_state(AdminState.waiting_for_user_id)

@dp.message(AdminState.waiting_for_user_id)
async def process_admin_user_id(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    
    if user_input.isdigit():
        user_id = int(user_input)
    else:
        username = user_input.lstrip('@')
        user_id = user_db.get_user_id_by_username(username)
    
    if not user_id:
        await message.answer(f"Пользователь {user_input} не найден. Убедитесь, что он хотя бы раз написал боту /start")
        await state.clear()
        return
    
    await state.update_data(user_id=user_id)
    await message.answer(
        f"Найден пользователь ID: {user_id}\n\n"
        "Выберите тип подписки:\n\n"
        "1 - Навсегда (бессрочно)\n"
        "2 - На количество дней\n\n"
        "Отправьте 1 или 2"
    )
    await state.set_state(AdminState.waiting_for_days)

@dp.message(AdminState.waiting_for_days)
async def process_admin_days(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get("user_id")
    choice = message.text.strip()
    
    user_info = user_db.get_user_info(user_id)
    user_name = user_info.get('first_name', f"ID {user_id}") if user_info else f"ID {user_id}"
    
    if choice == "1":
        user_db.activate_forever_subscription(user_id)
        await message.answer(f"Пользователю {user_name} выдана бессрочная подписка!")
        await state.clear()
        
        try:
            await bot.send_message(
                user_id,
                "Вам выдана бессрочная подписка! Теперь вы можете пользоваться ботом без ограничений."
            )
        except:
            await message.answer("Не удалось отправить уведомление пользователю")
            
    elif choice == "2":
        await message.answer("Введите количество дней (например: 30)")
        await state.set_state(AdminState.waiting_for_days_value)
    else:
        await message.answer("Пожалуйста, введите 1 или 2")

@dp.message(AdminState.waiting_for_days_value)
async def process_admin_days_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get("user_id")
    
    try:
        days = int(message.text.strip())
        user_db.activate_subscription(user_id, days)
        
        user_info = user_db.get_user_info(user_id)
        user_name = user_info.get('first_name', f"ID {user_id}") if user_info else f"ID {user_id}"
        
        await message.answer(f"Пользователю {user_name} выдана подписка на {days} дней!")
        await state.clear()
        
        try:
            await bot.send_message(
                user_id,
                f"Вам выдана подписка на {days} дней! Теперь вы можете пользоваться ботом без ограничений.\n\nОсталось дней: {days}"
            )
        except:
            await message.answer("Не удалось отправить уведомление пользователю")
            
    except ValueError:
        await message.answer("Неверное количество дней. Введите число.")
        await state.clear()

@dp.message(Command("admin_remove_user"))
async def cmd_admin_remove_user(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /admin_remove_user user_id или @username")
        return
    
    try:
        user_id = await get_user_id_or_username(parts[1])
        if not user_id:
            await message.answer(f"Пользователь {parts[1]} не найден")
            return
        
        user_info = user_db.get_user_info(user_id)
        user_name = user_info.get('first_name', f"ID {user_id}") if user_info else f"ID {user_id}"
        
        user_db.clear_all_user_data(user_id)
        await message.answer(f"Пользователь {user_name} удалён. Все данные очищены.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(Command("admin_extend"))
async def cmd_admin_extend(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: /admin_extend user_id или @username days")
        return
    
    try:
        user_id = await get_user_id_or_username(parts[1])
        if not user_id:
            await message.answer(f"Пользователь {parts[1]} не найден")
            return
        
        days = int(parts[2])
        user_db.extend_subscription(user_id, days)
        
        user_info = user_db.get_user_info(user_id)
        user_name = user_info.get('first_name', f"ID {user_id}") if user_info else f"ID {user_id}"
        subscription = user_db.get_subscription_status(user_id)
        
        await message.answer(f"Подписка пользователя {user_name} продлена на {days} дней!")
        
        try:
            await bot.send_message(
                user_id,
                f"Ваша подписка продлена на {days} дней!\n\nОсталось дней: {subscription['days_left']}"
            )
        except:
            await message.answer("Не удалось отправить уведомление пользователю")
            
    except ValueError:
        await message.answer("Неверное количество дней. Введите число.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(Command("admin_info"))
async def cmd_admin_info(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /admin_info user_id или @username")
        return
    
    try:
        user_id = await get_user_id_or_username(parts[1])
        if not user_id:
            await message.answer(f"Пользователь {parts[1]} не найден")
            return
        
        user_info = user_db.get_user_info(user_id)
        
        if not user_info:
            await message.answer(f"Пользователь {parts[1]} не найден")
            return
        
        text = f"Информация о пользователе {parts[1]} (ID: {user_id})\n\n"
        text += f"Имя: {user_info.get('first_name', 'Не указано')}\n"
        text += f"Username: @{user_info.get('username', 'нет')}\n"
        text += f"Зарегистрирован: {format_date(user_info.get('created_at', ''))}\n"
        text += f"Статистика за сегодня:\n"
        text += f"   Калории: {user_info.get('calories', 0):.0f} ккал\n"
        text += f"   Белки: {user_info.get('protein', 0):.1f} г\n"
        text += f"   Жиры: {user_info.get('fat', 0):.1f} г\n"
        text += f"   Углеводы: {user_info.get('carbs', 0):.1f} г\n"
        
        sub = user_info.get('subscription', {})
        if sub.get('is_forever'):
            text += f"Подписка: бессрочная\n"
        elif sub.get('paid_until'):
            text += f"Подписка: до {format_date(sub['paid_until'])}\n"
        elif sub.get('trial_end'):
            text += f"Тестовый период: до {format_date(sub['trial_end'])}\n"
        
        ref_stats = user_info.get('referral_stats', {})
        text += f"\nРеферальная статистика:\n"
        text += f"   Привёл: {ref_stats.get('total_refs', 0)} пользователей\n"
        text += f"   Оплатили: {ref_stats.get('paid_refs', 0)}\n"
        text += f"   Сумма к выплате: {ref_stats.get('total_commission', 0):.0f} ₽"
        
        await message.answer(text)
        
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message(Command("admin_users"))
async def cmd_admin_users(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    users = user_db.get_all_users()
    if not users:
        await message.answer("Нет пользователей")
        return
    
    text = "Список пользователей:\n\n"
    for u in users:
        text += f"ID: {u['user_id']}\n"
        text += f"Имя: {u['first_name']}\n"
        if u['username']:
            text += f"Username: @{u['username']}\n"
        text += f"Регистрация: {format_date(u['created_at'])}\n"
        
        if u.get('is_forever'):
            text += f"Подписка: бессрочная\n"
        elif u.get('paid_until'):
            text += f"Оплачено до: {format_date(u['paid_until'])}\n"
        elif u.get('trial_end'):
            text += f"Триал до: {format_date(u['trial_end'])}\n"
        text += "─" * 20 + "\n"
    
    await message.answer(text)

@dp.message(Command("admin_activate"))
async def cmd_admin_activate(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /admin_activate user_id или @username [days]")
        return
    
    try:
        user_id = await get_user_id_or_username(parts[1])
        if not user_id:
            await message.answer(f"Пользователь {parts[1]} не найден")
            return
        
        days = int(parts[2]) if len(parts) > 2 else 30
        user_db.activate_subscription(user_id, days)
        
        user_info = user_db.get_user_info(user_id)
        user_name = user_info.get('first_name', f"ID {user_id}") if user_info else f"ID {user_id}"
        
        await message.answer(f"Подписка активирована для {user_name} на {days} дней")
        
        try:
            await bot.send_message(
                user_id,
                f"Ваша подписка активирована на {days} дней!\n\nТеперь вы можете пользоваться ботом без ограничений.\nОсталось дней: {days}"
            )
        except:
            await message.answer("Не удалось отправить уведомление пользователю")
            
    except ValueError:
        await message.answer("Неверное количество дней. Введите число.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

# ============ РЕФЕРАЛЬНЫЕ КОМАНДЫ ============

@dp.message(Command("ref"))
async def cmd_create_referral(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer(
            "Использование: /ref @username процент месяцы\n\n"
            "Пример: /ref @john 50 12\n"
            "Пример: /ref @jane 20 1"
        )
        return
    
    username = parts[1].lstrip('@')
    try:
        commission_percent = int(parts[2])
        bonus_months = int(parts[3])
    except ValueError:
        await message.answer("Процент и месяцы должны быть числами")
        return
    
    if commission_percent < 0 or commission_percent > 100:
        await message.answer("Процент должен быть от 0 до 100")
        return
    
    if bonus_months < 0:
        await message.answer("Количество месяцев не может быть отрицательным")
        return
    
    code = user_db.generate_referral_link(username, commission_percent, bonus_months)
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={code}"
    
    await message.answer(
        f"Реферальная ссылка создана для @{username}\n\n"
        f"Ссылка: {link}\n\n"
        f"Условия:\n"
        f"• Комиссия: {commission_percent}% от оплат\n"
        f"• Бонус рефералу: {bonus_months} месяц(ев) бесплатной подписки\n\n"
        f"При переходе по ссылке новый пользователь получит +3 дня к тестовому периоду.\n"
        f"При оплате подписки рефералу начислится комиссия.\n\n"
        f"Пользователь @{username} получит бонусные месяцы после первого запуска бота."
    )

@dp.message(Command("ref_stats"))
async def cmd_ref_stats(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    stats = user_db.get_referral_stats()
    
    if not stats:
        await message.answer("Нет реферальных ссылок")
        return
    
    text = "Статистика рефералов:\n\n"
    total_refs = 0
    total_paid = 0
    total_commission = 0
    
    for i, s in enumerate(stats, 1):
        username = f"@{s['username']}" if s['username'] else s['first_name']
        text += f"{i}. {username}\n"
        text += f"   Комиссия: {s['commission_percent']}% | Бонус: {s['bonus_months']} мес\n"
        text += f"   Привёл: {s['total_refs']} (оплатили: {s['paid_refs']})\n"
        text += f"   Сумма к выплате: {s['total_commission']:.0f} ₽\n\n"
        
        total_refs += s['total_refs']
        total_paid += s['paid_refs']
        total_commission += s['total_commission']
    
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"Всего приведено: {total_refs}\n"
    text += f"Всего оплатили: {total_paid}\n"
    text += f"Общая сумма к выплате: {total_commission:.0f} ₽"
    
    await message.answer(text)

@dp.message(Command("ref_link_info"))
async def cmd_ref_link_info(message: types.Message):
    if not is_admin(message.from_user.id, message.from_user.username):
        await message.answer("Нет доступа")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /ref_link_info код_ссылки")
        return
    
    code = parts[1]
    info = user_db.get_referral_link_info(code)
    
    if not info:
        await message.answer(f"Ссылка с кодом {code} не найдена")
        return
    
    username = f"@{info['username']}" if info['username'] else info['first_name']
    bot_info = await bot.get_me()
    
    text = f"Информация о реферальной ссылке\n\n"
    text += f"Ссылка: https://t.me/{bot_info.username}?start={code}\n"
    text += f"Реферал: {username}\n"
    text += f"Комиссия: {info['commission_percent']}%\n"
    text += f"Бонус рефералу: {info['bonus_months']} мес\n"
    text += f"Создана: {format_date(info['created_at'])}\n"
    text += f"Статистика:\n"
    text += f"   Переходов: {info['total_refs']}\n"
    text += f"   Оплатили: {info['paid_refs']}"
    
    await message.answer(text)

# ============ ПРОФИЛЬ ============

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message, state: FSMContext):
    profile = user_db.get_profile(message.from_user.id)
    
    if profile:
        bmr = user_db.calculate_bmr(profile)
        tdee = user_db.calculate_tdee(profile)
        activity_name = ACTIVITY_LEVELS.get(profile["activity_level"], {"name": "Не указано"})["name"]
        gender_text = "Мужской" if profile["gender"] == "male" else "Женский"
        
        await message.answer(
            f"Ваш профиль\n\n"
            f"Имя: {profile['name']}\n"
            f"Вес: {profile['weight']} кг\n"
            f"Рост: {profile['height']} см\n"
            f"Возраст: {profile['age']} лет\n"
            f"Пол: {gender_text}\n"
            f"Активность: {activity_name}\n\n"
            f"Расчёты:\n"
            f"Базовый метаболизм (BMR): {bmr:.0f} ккал\n"
            f"Суточная норма (TDEE): {tdee:.0f} ккал\n\n"
            f"Чтобы изменить данные, используйте /profile_edit"
        )
    else:
        await message.answer(
            "Давайте познакомимся!\n\n"
            "Как вас зовут? (напишите имя)"
        )
        await state.set_state(ProfileState.waiting_for_name)

@dp.message(Command("profile_edit"))
async def cmd_profile_edit(message: types.Message, state: FSMContext):
    await message.answer("Как вас зовут? (напишите имя)")
    await state.set_state(ProfileState.waiting_for_name)

@dp.message(ProfileState.waiting_for_name)
async def process_profile_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("Сколько вам лет? (напишите число)")
    await state.set_state(ProfileState.waiting_for_age)

@dp.message(ProfileState.waiting_for_age)
async def process_profile_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        await state.update_data(age=age)
        await message.answer("Ваш вес? (в кг, например: 75)")
        await state.set_state(ProfileState.waiting_for_weight)
    except ValueError:
        await message.answer("Пожалуйста, введите число (например: 30)")

@dp.message(ProfileState.waiting_for_weight)
async def process_profile_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.strip().replace(',', '.'))
        await state.update_data(weight=weight)
        await message.answer("Ваш рост? (в см, например: 175)")
        await state.set_state(ProfileState.waiting_for_height)
    except ValueError:
        await message.answer("Пожалуйста, введите число (например: 75.5)")

@dp.message(ProfileState.waiting_for_height)
async def process_profile_height(message: types.Message, state: FSMContext):
    try:
        height = float(message.text.strip().replace(',', '.'))
        await state.update_data(height=height)
        await message.answer("Ваш пол?", reply_markup=get_gender_keyboard())
        await state.set_state(ProfileState.waiting_for_gender)
    except ValueError:
        await message.answer("Пожалуйста, введите число (например: 175)")

@dp.callback_query(lambda c: c.data.startswith("gender_"))
async def process_profile_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = "male" if callback.data == "gender_male" else "female"
    await state.update_data(gender=gender)
    await callback.message.edit_text("Ваш уровень физической активности?", reply_markup=get_activity_keyboard())
    await state.set_state(ProfileState.waiting_for_activity)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("activity_"))
async def process_profile_activity(callback: types.CallbackQuery, state: FSMContext):
    activity_level = callback.data.replace("activity_", "")
    await state.update_data(activity_level=activity_level)
    
    data = await state.get_data()
    user_db.save_profile(callback.from_user.id, data)
    
    tdee = user_db.calculate_tdee(data)
    
    await callback.message.edit_text(
        f"Профиль сохранён!\n\n"
        f"Имя: {data['name']}\n"
        f"Возраст: {data['age']} лет\n"
        f"Вес: {data['weight']} кг\n"
        f"Рост: {data['height']} см\n"
        f"Активность: {ACTIVITY_LEVELS[activity_level]['name']}\n\n"
        f"Ваша суточная норма калорий: {tdee:.0f} ккал\n\n"
        f"Теперь статистика будет показывать процент от нормы!"
    )
    await state.clear()
    await callback.answer()

# ============ ОСНОВНЫЕ КОМАНДЫ ============

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    args = message.text.split()
    referral_code = None
    if len(args) > 1:
        referral_code = args[1]
        if not referral_code.startswith('ref_'):
            referral_code = None
    
    user, is_new = user_db.get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        referral_code
    )
    
    if is_new:
        await notify_admin(message.from_user.id, message.from_user.username, message.from_user.first_name)
        
        if referral_code:
            await message.answer(
                "Добро пожаловать!\n\n"
                "Вы перешли по реферальной ссылке и получили +3 дня к тестовому периоду!"
            )
    
    subscription = user_db.get_subscription_status(message.from_user.id)
    profile = user_db.get_profile(message.from_user.id)
    
    sub_status = format_subscription_status(subscription)
    
    welcome_text = (
        f"🍏 Привет! Я FoodTracker — твой умный дневник питания.\n\n"
        f"🥗 Расскажи, что съел — я посчитаю калории, белки, жиры и углеводы.\n"
        f"📊 Если заполнишь профиль — покажу процент от дневной нормы.\n\n"
        f"✨ {sub_status}\n\n"
        f"Попробуй прямо сейчас: «гречка 150г, отварная куриная грудка 150 грамм, салат из огурцов и томатов с оливковым маслом 150 грамм»"
    )
    
    if not profile:
        welcome_text += "\n\n📝 Давай познакомимся! Используй /profile чтобы я подсказывал твою норму."
    
    await message.answer(welcome_text)

@dp.message(Command("subscription"))
async def cmd_subscription(message: types.Message):
    subscription = user_db.get_subscription_status(message.from_user.id)
    await message.answer(f"Статус подписки: {format_subscription_status(subscription)}")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "Помощь:\n\n"
        "/stats — статистика за сегодня\n"
        "/history — история записей\n"
        "/clear — очистить статистику\n"
        "/profile — мой профиль\n"
        "/profile_edit — изменить профиль\n"
        "/subscription — статус подписки\n\n"
        "Просто напишите, что съели, например:\n"
        "борщ 400г\n"
        "яичница 4 яйца\n"
        "гречка 200г, курица 150\n\n"
        f"Связаться с админом: {ADMIN_CONTACT}\n\n"
        "Администраторам: /admin_panel"
    )
    
    await message.answer(help_text)

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    subscription = user_db.get_subscription_status(message.from_user.id)
    if subscription["days_left"] <= 0 and not subscription["is_active"] and not subscription.get("is_forever"):
        await message.answer(f"Ваш тестовый период истёк.\n\nДля продолжения использования оформите подписку: {ADMIN_CONTACT}")
        return
    
    stats = user_db.get_today_stats(message.from_user.id)
    profile = user_db.get_profile(message.from_user.id)
    tdee = user_db.calculate_tdee(profile) if profile else None
    
    await message.answer(format_daily_stats(stats, tdee))

@dp.message(Command("history"))
async def cmd_history(message: types.Message):
    subscription = user_db.get_subscription_status(message.from_user.id)
    if subscription["days_left"] <= 0 and not subscription["is_active"] and not subscription.get("is_forever"):
        await message.answer(f"Ваш тестовый период истёк.\n\nДля продолжения использования оформите подписку: {ADMIN_CONTACT}")
        return
    
    meals = user_db.get_recent_meals(message.from_user.id, 10)
    if not meals:
        await message.answer("История пуста.")
        return
    text = "Последние записи:\n\n"
    for meal in meals:
        weight = meal.get("weight_grams", 0)
        text += f"{meal['product_name']} - {weight}г — {meal['calories']:.0f} ккал\n"
    await message.answer(text)

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да", callback_data="clear_confirm"),
         InlineKeyboardButton(text="Нет", callback_data="clear_cancel")]
    ])
    await message.answer("Очистить статистику за сегодня?", reply_markup=markup)

@dp.callback_query(lambda c: c.data.startswith("clear_"))
async def handle_clear_callback(callback: types.CallbackQuery):
    if callback.data == "clear_confirm":
        user_db.clear_today(callback.from_user.id)
        await callback.message.edit_text("Статистика очищена!")
    else:
        await callback.message.edit_text("Отменено.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("confirm_"))
async def handle_confirmation_callback(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data.replace("confirm_", "")
    data = await state.get_data()
    original_products = data.get("original_products", [])
    
    if action == "yes":
        for p in original_products:
            product_data = extract_product_data(p)
            user_db.add_meal(callback.from_user.id, product_data)
        
        stats = user_db.get_today_stats(callback.from_user.id)
        profile = user_db.get_profile(callback.from_user.id)
        tdee = user_db.calculate_tdee(profile) if profile else None
        
        response = f"✅ Сохранено!\n\n{format_daily_stats(stats, tdee)}"
        
        if not has_profile(callback.from_user.id):
            response += "\n\nЕсли мы познакомимся, то я могу давать больше информации.\nИспользуйте команду /profile для настройки."
        
        await callback.message.edit_text(response)
        await state.clear()
        
    else:
        await callback.message.edit_text(
            "❌ Не записано.\n\n"
            "Напишите правильные данные, например:\n"
            "борщ 300г\n"
            "кефир 200г\n"
            "или\n"
            "удали яйца"
        )
    
    await callback.answer()

# ============ ОБРАБОТЧИК КОРРЕКЦИИ ============

@dp.message(WaitingState.waiting_for_correction)
async def handle_correction(message: types.Message, state: FSMContext):
    # Пропускаем сообщения без текста
    if not message.text:
        return
    
    user_text = message.text.strip()
    user_text_lower = user_text.lower()
    data = await state.get_data()
    original_products = data.get("original_products", [])
    
    if is_affirmative(user_text_lower):
        for p in original_products:
            product_data = extract_product_data(p)
            user_db.add_meal(message.from_user.id, product_data)
        
        stats = user_db.get_today_stats(message.from_user.id)
        profile = user_db.get_profile(message.from_user.id)
        tdee = user_db.calculate_tdee(profile) if profile else None
        
        response = f"Сохранено!\n\n{format_daily_stats(stats, tdee)}"
        
        if not has_profile(message.from_user.id):
            response += "\n\nЕсли мы познакомимся, то я могу давать больше информации.\nИспользуйте команду /profile для настройки."
        
        await message.answer(response)
        await state.clear()
        return
    
    if is_negative(user_text_lower) and not is_correction(user_text_lower):
        await message.answer(
            "Напишите правильные данные, например:\n"
            "борщ 300г\n"
            "кефир 200г\n"
            "или\n"
            "удали яйца"
        )
        return
    
    if is_delete_command(user_text_lower):
        words_to_delete = re.findall(r'[\w]+', user_text.replace("удали", "").replace("убрать", "").replace("удалить", ""))
        if words_to_delete:
            to_delete = words_to_delete[0]
            new_products = []
            for p in original_products:
                if to_delete not in p.get("name", "").lower():
                    new_products.append(p)
            
            if len(new_products) == len(original_products):
                await message.answer(f"Не найден продукт '{to_delete}' для удаления.")
                return
            
            total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
            for p in new_products:
                total["calories"] += p.get("calories", 0)
                total["protein"] += p.get("protein", 0)
                total["fat"] += p.get("fat", 0)
                total["carbs"] += p.get("carbs", 0)
            
            lines = []
            for p in new_products:
                name = p.get("name", "")
                weight = p.get("weight_grams", 0)
                cal = p.get("calories", 0)
                prot = p.get("protein", 0)
                fat = p.get("fat", 0)
                carbs = p.get("carbs", 0)
                lines.append(f"{name} - {weight}г, К {cal:.0f}, Б {prot:.1f}, Ж {fat:.1f}, У {carbs:.1f}")
            
            result_text = "Обновлено:\n\n" + "\n".join(lines)
            result_text += f"\n\nИТОГО: {total['calories']:.0f} ккал | Б: {total['protein']:.1f}г | Ж: {total['fat']:.1f}г | У: {total['carbs']:.1f}г"
            result_text += "\n\nЗаписываю?"
            
            await state.update_data(original_products=new_products)
            await message.answer(result_text, reply_markup=get_confirmation_keyboard())
        return
    
    if user_text.startswith('/'):
        await state.clear()
        await handle_message(message, state)
        return
    
    if is_correction(user_text_lower):
        waiting_msg = await message.answer("Пересчитываю...")
        result = await food_search.parse_and_calculate(user_text)
        await waiting_msg.delete()
        
        if not result["success"] or not result["data"].get("products"):
            await message.answer(
                "Не удалось распознать корректировку. Напишите, например:\n"
                "борщ 300г\n"
                "кефир 200г"
            )
            return
        
        new_products = result["data"].get("products", [])
        total = result["data"].get("total", {})
        
        lines = []
        for p in new_products:
            name = p.get("name", "")
            weight = p.get("weight_grams", 0)
            cal = p.get("calories", 0)
            prot = p.get("protein", 0)
            fat = p.get("fat", 0)
            carbs = p.get("carbs", 0)
            lines.append(f"{name} - {weight}г, К {cal:.0f}, Б {prot:.1f}, Ж {fat:.1f}, У {carbs:.1f}")
        
        result_text = "Обновлено:\n\n" + "\n".join(lines)
        result_text += f"\n\nИТОГО: {total['calories']:.0f} ккал | Б: {total['protein']:.1f}г | Ж: {total['fat']:.1f}г | У: {total['carbs']:.1f}г"
        result_text += "\n\nЗаписываю?"
        
        await state.update_data(original_products=new_products)
        await message.answer(result_text, reply_markup=get_confirmation_keyboard())
        return
    
    await message.answer(
        "Не понял. Напишите:\n"
        "• новые данные, например: борщ 300г, кефир 200г\n"
        "• 'удали X' — чтобы удалить продукт\n"
        "Или нажмите кнопки выше."
    )

# ============ ОСНОВНОЙ ОБРАБОТЧИК ============

@dp.message(lambda message: message.text)
async def handle_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    subscription = user_db.get_subscription_status(user_id)
    if subscription["days_left"] <= 0 and not subscription["is_active"] and not subscription.get("is_forever"):
        await message.answer(
            f"Ваш тестовый период истёк.\n\n"
            f"Для продолжения использования оформите подписку за {SUBSCRIPTION_PRICE}₽/мес.\n"
            f"Свяжитесь с админом: {ADMIN_CONTACT}"
        )
        return
    
    current_state = await state.get_state()
    if current_state == WaitingState.waiting_for_correction.state:
        await handle_correction(message, state)
        return
    
    waiting_msg = await message.answer("Считаю...")
    await bot.send_chat_action(message.chat.id, "typing")
    
    result = await food_search.parse_and_calculate(message.text)
    
    await waiting_msg.delete()
    
    if not result["success"] or not result["data"].get("products"):
        await message.answer("Не удалось обработать. Попробуйте написать по-другому, например:\nборщ 400г\nяичница 4 яйца\nстакан кефира")
        return
    
    data = result["data"]
    products = data.get("products", [])
    user_text = result.get("user_text", "")
    
    if not products:
        await message.answer("Не удалось распознать продукты.")
        return
    
    await state.set_state(WaitingState.waiting_for_correction)
    await state.update_data(original_products=products, original_message=message.text)
    
    if user_text:
        full_text = user_text + "\n\nЗаписываю?"
    else:
        lines = []
        for p in products:
            name = p.get("name", "")
            weight = p.get("weight_grams", 0)
            cal = p.get("calories", 0)
            prot = p.get("protein", 0)
            fat = p.get("fat", 0)
            carbs = p.get("carbs", 0)
            lines.append(f"{name} - {weight}г, К {cal:.0f}, Б {prot:.1f}, Ж {fat:.1f}, У {carbs:.1f}")
        
        total = data.get("total", {})
        result_text = "\n".join(lines)
        result_text += f"\n\nИТОГО: {total.get('calories', 0):.0f} ккал | Б: {total.get('protein', 0):.1f}г | Ж: {total.get('fat', 0):.1f}г | У: {total.get('carbs', 0):.1f}г"
        result_text += "\n\nЗаписываю?"
        full_text = result_text
    
    await message.answer(full_text, reply_markup=get_confirmation_keyboard())

# ============ ЗАПУСК ============

async def main():
    await set_bot_commands()
    print("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())