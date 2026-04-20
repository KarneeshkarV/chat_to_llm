from __future__ import annotations

import logging
import warnings

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore")

log_config = uvicorn.config.LOGGING_CONFIG
log_config["formatters"]["default"]["fmt"] = "%(asctime)s | %(levelname)s | %(message)s"
log_config["formatters"]["access"]["fmt"] = (
    r"%(asctime)s | %(levelname)s | %(client_addr)s: %(request_line)s %(status_code)s"
)

app = FastAPI(
    title="Chat-to-LLM",
    description="Local ChatGPT-to-OpenAI API server using browser cookies",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.security_scheme = HTTPBearer()

from api.chat import register_routes  # noqa: E402

register_routes(app)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)
logger.info("Chat-to-LLM server starting")

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
