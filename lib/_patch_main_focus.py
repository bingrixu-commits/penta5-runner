from pathlib import Path

runner = Path("/root/penta5-runner/penta5_backend_runner.js")
print("exists", runner.exists(), "size", runner.stat().st.st_size if runner.exists() else 0)
