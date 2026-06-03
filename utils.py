import time
import re

def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def now_ts() -> int:
    return int(time.time())

def fmt(val):
    return f"{val:.2f}" if isinstance(val, (int, float)) else "-"
