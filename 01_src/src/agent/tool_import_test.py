"""Legacy registry remains accessible without eagerly importing domain plugins."""
from pathlib import Path
import subprocess
import sys


def test_foundational_tool_package_does_not_import_research():
    root = str(Path(__file__).resolve().parents[2])
    script = '''
import sys, importlib.abc
class BlockDomains(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"src.config", "src.research", "src.figures"} or fullname.startswith(("src.research.", "src.figures.")):
            raise AssertionError("unexpected domain/config import: " + fullname)
sys.meta_path.insert(0, BlockDomains())
sys.path.insert(0, sys.argv[1])
import src.tools
from src.tools import outcome
assert "_TOOL_REGISTRY" in dir(src.tools)
assert "src.tools.legacy_registry" not in sys.modules
assert "src.research" not in sys.modules
print("isolated")
'''
    result = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", script, root],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"
