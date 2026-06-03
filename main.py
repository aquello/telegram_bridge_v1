import logging
from .listener import TelegramSignalListener
from .logger import setup_logging

setup_logging("telegram_bridge_v1")
logger = logging.getLogger(__name__)
logger.info("Iniciando Telegram Bridge v1...")

API_ID   = 2395042
API_HASH = "77287044cf5b6582e3cac8b0d2f25c2e"

def main():
    listener = TelegramSignalListener(API_ID, API_HASH)
    listener.run_forever()

if __name__ == "__main__":
    main()
