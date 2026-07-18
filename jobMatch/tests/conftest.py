import os
import sys
import math

os.environ.setdefault("NVIDIA_API_KEY", "test-nvidia-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openrouter-key")
os.environ.setdefault("MONGO_USERNAME", "test-user")
os.environ.setdefault("MONGO_PASSWORD", "test-pass")

# app.py has `import Math` (line 9) but Python's module is `math` (lowercase).
# This workaround lets us import the module without modifying source code.
sys.modules["Math"] = math
