from dotenv import load_dotenv
import os

load_dotenv()

# Tokens & API keys
HF_TOKEN = os.getenv("HF_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

OLLAMA_ENDPOINT = "http://localhost:11434/api/chat"

# URLs & Configs for tools
DUCKDUCKGO_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"

OPEN_ROUTER_MODEL_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPEN_ROUTER_MODEL_HEADERS = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",}

TAVILY_HEADERS = {"Authorization": f"Bearer {TAVILY_API_KEY}", "Content-Type": "application/json",}
