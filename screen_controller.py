#!/usr/bin/env python3
"""
Screen Controller: Screenshot → AI Description → xdotool Click Loop
===================================================================

A closed-loop system that:
  1. Captures a screenshot of the screen
  2. Sends it to an AI vision model for description & element detection
  3. Maps the AI's output to xdotool click/move commands
  4. Repeats until a terminal condition is met

Requirements (install once):
  pip install pillow openai opencv-python
  sudo apt install xdotool scrot   (or: brew install xdotool scrot)

Architecture:
  ┌──────────┐    screenshot    ┌─────────────┐   instructions   ┌──────────┐
  │  Capture  │ ──────────────▶ │  AI Vision  │ ──────────────▶ │ xdotool  │
  │  Module   │                 │  (GPT-4V /  │                  │  Module  │
  │ (scrot/   │ ◀────────────── │  Gemini /   │                  │ (click/  │
  │  grim)    │    feedback     │  Ollama     │                  │  move)   │
  └──────────┘                 └─────────────┘                  └──────────┘
        │                                                           │
        └─────────── Repeat until goal ──────────────────────────────┘
"""

import subprocess
import json
import time
import os
import base64
from pathlib import Path
from typing import Optional


# ─── CONFIG ───────────────────────────────────────────────────────────
SCREENSHOT_DIR = Path("/tmp/screen_ctrl")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOT_INTERVAL = 2.0  # seconds between loops
MAX_ITERATIONS = 50
SYSTEM_PROMPT = """You are a computer-use AI assistant. You see a screenshot of a desktop.
Your job is to output a JSON array of actions to perform. Each action should have:
  - type: "click" | "move" | "double_click" | "right_click" | "type" | "scroll" | "done"
  - x: int (screen pixel coordinate)
  - y: int
  - text: str (for "type" actions, the text to type)
  - button: int (1=left, 2=middle, 3=right; default 1)
  - scroll: int (positive=up, negative=down; for "scroll" type)

Rules:
- Coordinates are in screen pixel space (top-left is 0,0)
- Be precise: target specific buttons, links, text fields
- If the task is complete, output: [{"type": "done", "x": 0, "y": 0}]
- If you need more info, output: [{"type": "done", "x": 0, "y": 0, "note": "need more info"}]

Task: {task}
"""


# ─── MODULE 1: SCREENSHOT CAPTURE ────────────────────────────────────
class ScreenshotCapture:
    """Capture screen images using various backends."""

    def __init__(self, method="scrot"):
        self.method = method
        self.output = SCREENSHOT_DIR / "current.png"

    def capture(self) -> Path:
        if self.method == "scrot":
            return self._scrot()
        elif self.method == "gnome-screenshot":
            return self._gnome_screenshot()
        elif self.method == "grim":
            return self._grim()
        elif self.method == "screencapture":  # macOS
            return self._screencapture()
        else:
            raise ValueError(f"Unknown capture method: {self.method}")

    def _scrot(self) -> Path:
        subprocess.run(["scrot", str(self.output), "--delay=1"], check=True)
        return self.output

    def _gnome_screenshot(self) -> Path:
        subprocess.run(
            ["gnome-screenshot", "-f", str(self.output), "--delay=1"], check=True
        )
        return self.output

    def _grim(self) -> Path:
        # grim outputs to stdout; slurp for region selection
        result = subprocess.run(
            ["grim", "-"], capture_output=True, check=True
        )
        self.output.write_bytes(result.stdout)
        return self.output

    def _screencapture(self) -> Path:
        subprocess.run(
            ["screencapture", str(self.output)], check=True
        )
        return self.output

    def get_base64(self) -> str:
        """Encode screenshot as base64 for AI vision APIs."""
        img = self.capture()
        with open(img, "rb") as f:
            return base64.b64encode(f.read()).decode()


# ─── MODULE 2: AI VISION ─────────────────────────────────────────────
class AIVisionEngine:
    """Send screenshots to an AI vision model and parse action instructions."""

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        try:
            from openai import OpenAI
            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            self.client = OpenAI(**client_kwargs)
        except ImportError:
            raise ImportError("Install with: pip install openai")

    def describe_and_act(self, screenshot_b64: str, task: str) -> list[dict]:
        """Send screenshot to AI and get structured action list."""
        prompt = SYSTEM_PROMPT.format(task=task)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{screenshot_b64}",
                                "detail": "high"
                            }
                        },
                        {"type": "text", "text": "Analyze this screenshot and output JSON actions."}
                    ]
                }
            ],
            max_tokens=500,
            temperature=0.0,
        )

        # Parse the response
        text = response.choices[0].message.content
        # Try to extract JSON from the response
        actions = self._extract_json(text)
        return actions

    @staticmethod
    def _extract_json(text: str) -> list[dict]:
        """Extract JSON array from model response (handles markdown code blocks)."""
        import re
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError(f"Could not parse actions from: {text}")

    def describe_only(self, screenshot_b64: str) -> str:
        """Get a plain-text description of the screen (no actions)."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe everything visible on this screen in detail. List all buttons, text fields, menus, icons, and their positions."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}", "detail": "high"}}
                    ]
                }
            ],
            max_tokens=1000,
        )
        return response.choices[0].message.content


# ─── MODULE 3: XDOTOOL INTERACTION ───────────────────────────────────
class XdotoolController:
    """Execute actions via xdotool."""

    def __init__(self, scale_x: float = 1.0, scale_y: float = 1.0):
        """
        scale_x, scale_y: Apply if screen resolution differs between
                          screenshot capture and AI coordinate space.
        """
        self.scale_x = scale_x
        self.scale_y = scale_y

    def execute(self, action: dict) -> bool:
        """Execute a single action dict. Returns True if successful."""
        action_type = action.get("type", "")

        if action_type == "done":
            note = action.get("note", "Task complete")
            print(f"✅ Done: {note}")
            return False  # signals loop to stop

        x = int(action.get("x", 0) * self.scale_x)
        y = int(action.get("y", 0) * self.scale_y)

        if action_type in ("move", "click", "double_click", "right_click", "scroll"):
            subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=True)

        if action_type == "click":
            btn = action.get("button", 1)
            subprocess.run(["xdotool", "click", str(btn)], check=True)
        elif action_type == "double_click":
            subprocess.run(["xdotool", "mousedown", "1", "mouseup", "1",
                            "mousedown", "1", "mouseup", "1"], check=True)
        elif action_type == "right_click":
            subprocess.run(["xdotool", "mousedown", "3", "mouseup", "3"], check=True)
        elif action_type == "type":
            text = action.get("text", "")
            subprocess.run(["xdotool", "type", "--delay", "10", "--clearmodifiers", text], check=True)
        elif action_type == "scroll":
            delta = action.get("scroll", -5)
            direction = "up" if delta > 0 else "down"
            steps = abs(delta)
            subprocess.run(["xdotool", "click", "--repeat", str(steps), direction], check=True)
        elif action_type == "key":
            key = action.get("key", "Return")
            subprocess.run(["xdotool", "key", key], check=True)
        elif action_type == "window_focus":
            window_name = action.get("window", "")
            result = subprocess.run(
                ["xdotool", "search", "--name", window_name],
                capture_output=True, text=True, check=True
            )
            win_id = result.stdout.strip()
            if win_id:
                subprocess.run(["xdotool", "windowfocus", "--sync", win_id], check=True)
            else:
                print(f"⚠️ Window '{window_name}' not found")
                return False

        print(f"  🖱️  {action_type}({x}, {y})" if action_type != "type" else f"  ⌨️  type: '{action.get('text','')}'")
        time.sleep(0.3)  # small delay between actions
        return True

    def move(self, x: int, y: int):
        """Just move the mouse."""
        subprocess.run(["xdotool", "mousemove", str(int(x * self.scale_x)), str(int(y * self.scale_y))])

    def click(self, x: int, y: int, button: int = 1):
        """Move to position and click."""
        subprocess.run(["xdotool", "mousemove", str(int(x * self.scale_x)), str(int(y * self.scale_y))])
        subprocess.run(["xdotool", "click", str(button)])

    def type_text(self, text: str):
        """Type text at current cursor position."""
        subprocess.run(["xdotool", "type", "--delay", "10", "--clearmodifiers", text])


# ─── MODULE 4: LOOP / ORCHESTRATOR ──────────────────────────────────
class ScreenControllerLoop:
    """Main loop: screenshot → AI → xdotool → repeat."""

    def __init__(self, task: str, api_key: str, model: str = "gpt-4o",
                 capture_method: str = "scrot", ai_base_url: Optional[str] = None):
        self.task = task
        self.capture = ScreenshotCapture(method=capture_method)
        self.vision = AIVisionEngine(api_key=api_key, model=model, base_url=ai_base_url)
        self.controller = XdotoolController()
        self.iteration = 0
        self.max_iterations = MAX_ITERATIONS

    def run(self):
        """Execute the main control loop."""
        print(f"🎯 Task: {self.task}")
        print(f"🔄 Starting loop (max {self.max_iterations} iterations)...\n")

        while self.iteration < self.max_iterations:
            self.iteration += 1
            print(f"{'='*60}")
            print(f"📸 Iteration {self.iteration}/{self.max_iterations}")

            # Step 1: Capture screenshot
            try:
                screenshot_path = self.capture.capture()
                screenshot_b64 = self.capture.get_base64()
                print(f"   Captured: {screenshot_path} ({os.path.getsize(screenshot_path)} bytes)")
            except Exception as e:
                print(f"   ❌ Screenshot failed: {e}")
                time.sleep(SCREENSHOT_INTERVAL)
                continue

            # Step 2: Get AI instructions
            try:
                actions = self.vision.describe_and_act(screenshot_b64, self.task)
                print(f"   AI returned {len(actions)} action(s)")
            except Exception as e:
                print(f"   ❌ AI failed: {e}")
                time.sleep(SCREENSHOT_INTERVAL)
                continue

            # Step 3: Execute actions
            still_going = True
            for action in actions:
                still_going = self.controller.execute(action)
                if not still_going:
                    break

            # Step 4: Check termination
            if not still_going:
                print(f"\n✅ Loop terminated at iteration {self.iteration}")
                return

            print(f"\n   ⏳ Waiting {SCREENSHOT_INTERVAL}s before next capture...\n")
            time.sleep(SCREENSHOT_INTERVAL)

        print(f"\n⚠️ Reached max iterations ({self.max_iterations}). Stopping.")

    def describe(self):
        """One-shot: just describe the screen (no clicking)."""
        screenshot_b64 = self.capture.get_base64()
        description = self.vision.describe_only(screenshot_b64)
        print(description)


# ─── USAGE EXAMPLES ─────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    API_KEY = os.environ.get("OPENAI_API_KEY", "")
    if not API_KEY:
        print("⚠️ Set OPENAI_API_KEY env var or edit this file.")
        print("   Usage: export OPENAI_API_KEY='sk-...'")
        sys.exit(1)

    # Example 1: Full autonomous loop
    if len(sys.argv) > 1:
        task = sys.argv[1]
        controller = ScreenControllerLoop(
            task=task,
            api_key=API_KEY,
            model="gpt-4o",
        )
        controller.run()
    else:
        print("Screen Controller - Screenshot → AI → xdotool Loop")
        print("=" * 60)
        print()
        print("USAGE:")
        print("  python screen_controller.py 'click on the login button'")
        print("  python screen_controller.py 'type hello into the first text field'")
        print("  python screen_controller.py 'navigate to google.com and search for X'")
        print()
        print("VARIABLES:")
        print("  export OPENAI_API_KEY='sk-...'     # or use any OpenAI-compatible API")
        print("  sudo apt install xdotool scrot     # system dependencies")
        print()
        print("ALTERNATIVE MODELS (OpenAI-compatible endpoints):")
        print("  # Ollama (local):")
        print("  python screen_controller.py 'task' --model llama3.2-vision")
        print("  python screen_controller.py 'task' --base-url http://localhost:11434/v1")
        print()
        print("  # Google Gemini:")
        print("  export OPENAI_API_KEY='AIza...'")
        print("  python screen_controller.py 'task' --model gemini-1.5-pro")
        print("  python screen_controller.py 'task' --base-url https://generativelanguage.googleapis.com/v1beta/openai/")
