import logging

from .channel_manager_gui import run_gui
from .logger import setup_logging

setup_logging("telegram_bridge_v1")
logger = logging.getLogger(__name__)
logger.info("Iniciando Telegram Bridge v1 GUI...")

API_ID = 2395042
API_HASH = "77287044cf5b6582e3cac8b0d2f25c2e"


def main():
    return run_gui(API_ID, API_HASH, session_name="tg_session_v1")


if __name__ == "__main__":
    main()
