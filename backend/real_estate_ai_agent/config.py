import os
from dotenv import load_dotenv

load_dotenv()

DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY", "")
DEEPINFRA_MODEL = os.getenv("DEEPINFRA_MODEL", "deepinfra/meta-llama/Meta-Llama-3.1-70B-Instruct")
REALESTATE_API_KEY = os.getenv("REALESTATE_API_KEY", "")
REDIS_URL = os.getenv("REDIS_URL", "")
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

_cors_raw = os.getenv("CORS_ORIGINS", "*").strip()
CORS_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()] or ["*"]
