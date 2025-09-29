import sys, os
sys.path.append(os.getcwd())
import importlib
m = importlib.import_module("app.ui.app")
print("loaded App class?", hasattr(m, "App"))
