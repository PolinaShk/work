import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import config
import processing
import telegram_bot
import logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger_main = logging.getLogger("main")

async def job_check_procedures(app):
    logger_main.info("Запуск проверки почты и B2B-Center...")
    await logger.log_action("🔄 Запуск проверки процедур", "info")
    async def notify_admin(text):
        await telegram_bot.send_admin_message(app, text)
    async def notify_partners(new_requests):
        await telegram_bot.notify_partners(app, new_requests)
    try:
        await processing.process_new_procedures(notify_admin, notify_partners)
    except Exception as e:
        logger_main.exception("Ошибка в job_check_procedures")
        error_msg = f"❌ Ошибка при проверке процедур: {str(e)[:300]}"
        await logger.log_action("Ошибка в job_check_procedures", "error", {"Ошибка": str(e)[:300]})
        await telegram_bot.send_admin_message(app, error_msg)

async def job_check_expiring(app):
    logger_main.info("Запуск проверки истекающих заявок...")
    await logger.log_action("⏰ Запуск проверки истекающих заявок", "info")
    async def notify_admin_with_keyboard(text):
        await telegram_bot.send_admin_message_with_confirm(app, text)
    async def notify_admin(text):
        await telegram_bot.send_admin_message(app, text)
    try:
        await processing.check_expiring(notify_admin_with_keyboard, notify_admin)
    except Exception as e:
        logger_main.exception("Ошибка в job_check_expiring")
        error_msg = f"❌ Ошибка при проверке сроков: {str(e)[:300]}"
        await logger.log_action("Ошибка в job_check_expiring", "error", {"Ошибка": str(e)[:300]})
        await telegram_bot.send_admin_message(app, error_msg)

async def run():
    if config.MISSING_ENV_VARS:
        logger_main.warning("Не заполнены переменные в .env: %s — бот запустится, но соответствующие функции работать не будут", ", ".join(config.MISSING_ENV_VARS))
    app = telegram_bot.build_application()
    logger.set_bot(app.bot)
    await logger.log_action("🚀 Бот запущен", "start", {"API": config.TELEGRAM_API_URL})
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(job_check_procedures, CronTrigger(hour=9, minute=0), args=[app])
    scheduler.add_job(job_check_procedures, CronTrigger(hour=17, minute=0), args=[app])
    scheduler.add_job(job_check_expiring, CronTrigger(day_of_week='mon-fri', hour='9-17', minute=30), args=[app])
    async with app:
        await app.start()
        await app.updater.start_polling()
        scheduler.start()
        logger_main.info("Бот запущен. Проверка почты: 9:00 и 17:00, проверка сроков: каждые 30 минут с 9:30 до 17:30 (пн-пт) (%s)", config.TIMEZONE)
        await logger.log_action("✅ Бот запущен", "success", {"Проверка почты": "9:00 и 17:00", "Проверка сроков": "каждые 30 минут, 9:30–17:30, пн–пт", "Часовой пояс": config.TIMEZONE})
        try:
            await asyncio.Event().wait()
        finally:
            scheduler.shutdown()
            await app.updater.stop()
            await app.stop()
            await logger.log_action("🛑 Бот остановлен", "warning")

if __name__ == "__main__":
    asyncio.run(run())
