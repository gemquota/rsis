"""Allow `python -m rsis` to work."""
import sys
from rsis.main import main

sys.exit(main())
