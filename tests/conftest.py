"""测试一律使用临时的 PKU_MCP_HOME；真实账号测试另取原来的目录。"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("PKU_MCP_LIVE_HOME", os.environ.get("PKU_MCP_HOME") or str(Path.home() / ".pku-mcp"))
os.environ["PKU_MCP_HOME"] = tempfile.mkdtemp(prefix="pku-mcp-test-")
os.environ["PKU_FILES_DIR"] = tempfile.mkdtemp(prefix="pku-mcp-files-")
