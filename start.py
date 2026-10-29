"""Cross-platform launcher; installs declared dependencies into a project-local venv."""
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    if sys.version_info < (3, 9):
        raise SystemExit("Python 3.9+ required: https://www.python.org/downloads/")
    env = root / ".venv"
    python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        print("Preparing InfoRelay / 正在准备运行环境…", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
    requirements = (root / "requirements.txt").read_bytes()
    marker = env / ".inforelay-requirements"
    if not marker.exists() or marker.read_bytes() != requirements:
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")], check=True)
        marker.write_bytes(requirements)
    subprocess.run([str(python), "-m", "info_relay", "serve", "--data-dir", str(root / ".inforelay")], cwd=root, check=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit("Startup failed. Check Python/network, then retry. / 启动失败，请检查 Python 与网络。\n" + type(error).__name__)
