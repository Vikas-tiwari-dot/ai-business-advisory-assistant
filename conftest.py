import sys
from pathlib import Path

# Allow `import scripts.generate_data` from tests/ without installing a package.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
