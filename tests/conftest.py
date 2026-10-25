"""Test configuration and fixtures."""
import os
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Set Phoenix working dir inside the project workspace
os.environ["PHOENIX_WORKING_DIR"] = os.path.join(PROJECT_ROOT, ".phoenix")
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["DISABLE_TRACING"] = "true"
os.environ["DISABLE_GRAPH_PREWARM"] = "true"
os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = "http://127.0.0.1:9999/dummy"
