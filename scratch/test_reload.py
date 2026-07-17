import os
import importlib
from dotenv import load_dotenv

# Create a dummy .env
with open(".env.test", "w") as f:
    f.write("TEST_VAR=123\n")

# Load it
load_dotenv(".env.test", override=True)
print("Before:", os.environ.get("TEST_VAR"))

# Update .env
with open(".env.test", "w") as f:
    f.write("TEST_VAR=456\n")

# Try loading again (simulating reload)
load_dotenv(".env.test", override=True)
print("After:", os.environ.get("TEST_VAR"))

