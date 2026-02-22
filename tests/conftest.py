import os
import sys

# Ensure slimclaw package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Set working directory to slimclaw so config.py resolves paths correctly
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
