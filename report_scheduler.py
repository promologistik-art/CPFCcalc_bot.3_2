"""
Планировщик ежедневных отчётов
"""
import asyncio
import logging
from datetime import datetime, timedelta
from config import REPORT_HOUR

logger = logging.getLogger(__name__)


class ReportScheduler:
    def __init__(self, bot, user_db, get_report_func):
        self.bot = bot
        self.user_db = user_db
        self.get_report_func = get_report_func
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._run_scheduler())
        logger.info(f"📊 Планировщик отчётов запущен (ежедневно в {REPORT_HOUR}:00 МСК)")

    async def stop(self):
        if self._task:
            self._task.cancel()
        logger.info("📊 Планировщик отчётов остановлен")

    async def _run_scheduler(self):
        while True:
            # Берём московское время (UTC+3)
            now_msk = datetime.utcnow() + timedelta(hours=3)
            report_time = now_msk.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
            
            if now_msk >= report_time:
                report_time = report_time + timedelta(days=1)
            
            # Переводим обратно в UTC для sleep
            report_time_utc = report_time - timedelta(hours=3)
            now_utc = datetime.utcnow()
            
            wait_seconds = (report_time_utc - now_utc).total_seconds()
            logger.info(f"📊 Следующий отчёт через {wait_seconds / 3600:.1f} часов (в {REPORT_HOUR}:00 МСК)")
            
            await asyncio.sleep(wait_seconds)
            await self._send_daily_reports()

    async def _send_daily_reports(self):
        logger.info("📊 Начинаем рассылку ежедневных отчётов...")
        
        today = datetime.utcnow().strftime("%Y-%m-%d")
        active_users = self.user_db.get_users_with_meals_today(today)
        
        if not active_users:
            logger.info("📊 Нет пользователей с приёмами пищи сегодня")
            return
        
        success = 0
        failed = 0
        
        for user_id in active_users:
            try:
                report_text = await self.get_report_func(user_id)
                await self.bot.send_message(user_id, report_text)
                success += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                failed += 1
                logger.error(f"Ошибка отправки отчёта {user_id}: {e}")
        
        logger.info(f"📊 Отчёты отправлены: {success} успешно, {failed} ошибок")


async def generate_daily_report(user_id: int, user_db) -> str:
    """
    Генерирует текст ежедневного отчёта
    
    Формат:
    🌙 Итоги дня: 24.04.2026
    
    🔥 1850 ккал из 2100 (88%)
    🥩 Белки: 95г | 🧈 Жиры: 62г | 🍞 Углеводы: 210г
    🍽 Приёмов пищи: 4
    
    Хороший день! 💪
    """
    stats = user_db.get_today_stats(user_id)
    meals_count = user_db.get_today_meals_count(user_id)
    profile = user_db.get_profile(user_id)
    
    today = datetime.utcnow().strftime("%d.%m.%Y")
    
    calories = stats.get('calories', 0)
    protein = stats.get('protein', 0)
    fat = stats.get('fat', 0)
    carbs = stats.get('carbs', 0)
    
    report = f"🌙 Итоги дня: {today}\n\n"
    
    if profile:
        tdee = user_db.calculate_tdee(profile)
        percent = min((calories / tdee * 100), 100) if tdee > 0 else 0
        report += f"🔥 {calories:.0f} ккал из {tdee:.0f} ({percent:.0f}%)\n"
    else:
        report += f"🔥 {calories:.0f} ккал\n"
    
    report += f"🥩 Белки: {protein:.1f}г | 🧈 Жиры: {fat:.1f}г | 🍞 Углеводы: {carbs:.1f}г\n"
    report += f"🍽 Приёмов пищи: {meals_count}\n\n"
    
    if meals_count == 0:
        report += "Не забудь записать приёмы пищи! 📝"
    elif profile:
        tdee = user_db.calculate_tdee(profile)
        percent = (calories / tdee * 100) if tdee > 0 else 0
        if percent >= 90:
            report += "Отличный день! 💪"
        elif percent >= 70:
            report += "Хороший день! 👏"
        elif percent >= 50:
            report += "Неплохо, но можно лучше! 🤔"
        else:
            report += "Сегодня маловато калорий. Завтра наверстай! 🍽"
    else:
        report += "Хороший день! 💪\nЗаполни /profile чтобы видеть проценты от нормы."
    
    return report