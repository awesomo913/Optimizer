"""Put the parent of this package on sys.path so tests can do
`from Optimizer import ...` while the package's own relative imports
(`from . import config`) keep working.
"""
import sys
from pathlib import Path

_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))
