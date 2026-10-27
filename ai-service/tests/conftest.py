import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AI_INTERNAL_TOKEN", "test")
os.environ.setdefault("OPENAI_MODEL", "test-model")
os.environ.setdefault("VAULT_KEY", "test")
