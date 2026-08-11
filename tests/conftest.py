from __future__ import annotations

import sys
from pathlib import Path


SERVICE_HUB_DIR = Path(__file__).resolve().parents[1] / "service-hub"
sys.path.insert(0, str(SERVICE_HUB_DIR))

