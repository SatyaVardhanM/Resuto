"""
frontend/bot_runner.py
──────────────────────
BotRunner — manages the bot subprocess (orchestrator.py).
No UI code. Imported by app.py only.
"""
import os
import sys
import subprocess
import threading

from frontend.constants import BOT_SCRIPT


class BotRunner:
    def __init__(self, key, args, on_line, on_done):
        self._key, self._args = key, args
        self._on_line, self._on_done = on_line, on_done
        self._proc = None

    def start(self):
        env = os.environ.copy()
        env.update({"ANTHROPIC_API_KEY": self._key,
                     "PYTHONIOENCODING": "utf-8",
                     "PYTHONUTF8":       "1",
                     "PYTHONUNBUFFERED": "1",
                     "BOT_GUI_MODE":     "1"})

        # Detect compiled (Nuitka or PyInstaller) vs source mode
        # Nuitka does NOT set sys.frozen — detect by checking if script exists on disk
        script = str(BOT_SCRIPT)
        is_compiled = (
            getattr(sys, "frozen", False)   # PyInstaller sets this
            or not os.path.exists(script)   # Nuitka: .py files compiled into binary
        )

        if is_compiled:
            # Compiled exe: spawn self with --bot-mode to run orchestrator
            cmd = [sys.executable, "--bot-mode", "--gui"] + self._args
        else:
            # Source mode: run orchestrator.py directly with Python
            cmd = [sys.executable, "-u", script, "--gui"] + self._args

        self._proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=0)
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, text: str):
        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.write(text + "\n")
                self._proc.stdin.flush()
        except Exception:
            pass

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def running(self): return self._proc is not None and self._proc.poll() is None

    def _read(self):
        try:
            for line in self._proc.stdout:
                self._on_line(line.rstrip())
        finally:
            self._on_done(self._proc.wait())