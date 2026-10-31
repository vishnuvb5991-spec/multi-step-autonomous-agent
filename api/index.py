import sys
from pathlib import Path

# Add project root to sys.path so modules (agent, mcp_servers, tracing, etc.) are importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app  # noqa: E402
