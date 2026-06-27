# Screen Control: Ideas & Approaches

## 1. Screenshot → AI → xdotool Loop (Your Idea)

**The core concept:** A closed-loop autonomous agent that sees the screen, thinks about what to do, and acts.

```
┌─────────────┐    PNG bytes     ┌───────────────────┐    JSON actions    ┌─────────────┐
│  Screenshot  │ ──────────────▶ │  Vision LLM (GPT-4V│ ──────────────▶ │  xdotool    │
│  Capture     │                 │   / Gemini / Ollama)│                  │  Controller │
│  (scrot/grim)│ ◀──────────────│                   │                  │             │
└─────────────┘   Next frame    └───────────────────┘                  └─────────────┘
        │                                                       │
        └─────────── Repeat until goal ──────────────────────────┘
```

**See:** `screen_controller.py` — a fully working implementation with:
- `ScreenshotCapture` — supports scrot, gnome-screenshot, grim, screencapture (macOS)
- `AIVisionEngine` — sends base64 image to any OpenAI-compatible API (GPT-4o, Gemini, Ollama)
- `XdotoolController` — maps AI output to mouse/keyboard actions
- `ScreenControllerLoop` — orchestrates the closed loop with configurable intervals

**Prompt template** sends a structured JSON action format:
```json
[
  {"type": "click", "x": 450, "y": 300},
  {"type": "type", "text": "hello"},
  {"type": "done"}
]
```

---

## 2. Other Screen Control Approaches

### A. DOM/Accessibility Tree → Actions (No Screenshots)
Instead of images, query the accessibility tree of the running application:

- **Linux/X11:** `xdotool getactivewindow`, `xprop`, `wmctrl`
- **Linux/Wayland:** `dbus` introspection, `at-spi2` (Accessibility Toolkit)
- **Browser:** `playwright` / `selenium` — interact with DOM directly
- **Web UI testing:** `pyautogui` + `pygetwindow`

**Pros:** Pixel-perfect precision, no AI vision needed for structured apps
**Cons:** Doesn't work for native apps without accessibility APIs, harder for arbitrary GUIs

### B. Computer Vision (OpenCV) + Heuristics
Use traditional CV to find UI elements, then click them:

```python
import cv2
import numpy as np

# Template match a button
template = cv2.imread("login_button.png", cv2.IMREAD_GRAYSCALE)
screenshot = cv2.imread("screen.png", cv2.IMREAD_GRAYSCALE)
result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
loc = np.where(result >= 0.8)
for pt in zip(*loc[::-1]):
    # Click center of match
    cx = pt[0] + template.shape[1] // 2
    cy = pt[1] + template.shape[0] // 2
    subprocess.run(["xdotool", "mousemove", str(cx), str(cy), "click", "1"])
```

**Pros:** Fast, no API calls, deterministic
**Cons:** Fragile — breaks with theme changes, different resolutions, dynamic content

### C. OCR + Regex + Click
Extract text via OCR, find target by text content, click near it:

```python
import pytesseract

screen = pyautogui.screenshot()
text_data = pytesseract.image_to_data(screen, output_type=pytesseract.Output.DICT)
# Find "Submit" button text coordinates
for i, word in enumerate(text_data['text']):
    if word.lower() == "submit":
        x = text_data['left'][i] + text_data['width'][i] // 2
        y = text_data['top'][i] + text_data['height'][i] // 2
        pyautogui.click(x, y)
```

**Pros:** Language-agnostic, works on any text label
**Cons:** OCR errors, slow, needs text to be present

### D. Semantic Segmentation / Object Detection
Run a neural net to detect UI elements (buttons, inputs, links):

```python
from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation
import torch

# Use a model like SAM (Segment Anything) or a custom UI element detector
processor = AutoImageProcessor.from_pretrained("facebook/sam-vit-base")
model = AutoModelForSemanticSegmentation.from_pretrained("facebook/sam-vit-base")
# ... segment the screenshot, find button regions, click centers
```

**Pros:** Can generalize to unseen UIs
**Cons:** Heavy, requires GPU, needs fine-tuning for desktop UI

### E. Hybrid: Vision LLM + OCR Grounding
Use the LLM for reasoning but OCR to get precise text locations:

```python
# 1. LLM describes what to find: "Find the 'Settings' gear icon"
# 2. OCR finds all text regions on screen
# 3. Match "Settings" text → click nearby icon
# 4. xdotool executes
```

This is more reliable than pure vision because you get exact bounding boxes from OCR.

### F. Window-Level Automation
Interact at the window/process level rather than pixel level:

- **Linux:** `wmctrl`, `xdotool search --name`, `xdotool windowfocus`
- **macOS:** `osascript -e 'tell application "System Events"'`
- **Cross-platform:** `pyautogui.getWindowsWithTitle()`

```python
import subprocess

# Focus a window by name
subprocess.run(["xdotool", "search", "--name", "Firefox", "windowfocus"])
subprocess.run(["xdotool", "key", "ctrl+l"])  # Focus URL bar
subprocess.run(["xdotool", "type", "google.com", "Return"])
```

**Pros:** Fast, doesn't need screenshots
**Cons:** Only works with windowed apps, not good for multi-window scenarios

---

## 3. Advanced Techniques

### A. State Tracking
Maintain a state machine across loop iterations:

```python
class ScreenControllerLoop:
    def __init__(self, task):
        self.state = "idle"
        self.history = []  # (screenshot_hash, action, result)
    
    def should_react(self, current_screenshot) -> bool:
        """Only re-run AI if the screen has actually changed."""
        hash_new = hash_image(current_screenshot)
        if hash_new == self.last_hash:
            return False
        self.last_hash = hash_new
        return True
```

This saves API calls when the screen is static.

### B. Keyboard Shortcuts First
Try hotkeys before clicking:
- `Alt+F4` to close windows
- `Ctrl+W` to close tabs
- `Ctrl+L` to focus URL bar in browsers
- `Super+D` to minimize all windows

### C. Region-of-Interest Capture
Only screenshot a specific region to reduce AI costs and latency:

```python
# scrot with geometry: -geometry WIDTHxHEIGHT+X+Y
subprocess.run(["scrot", "-geometry", "800x600+100+100", str(output)])
```

### D. Multi-Agent Architecture
Split the work into specialized agents:

```
┌──────────┐    coordinates    ┌──────────────┐
│  Planner  │ ◀─────────────── │  Executor     │
│ (LLM)     │  task breakdown  │ (xdotool)     │
└──────────┘                   └──────────────┘
       │
       │  screenshot
       ▼
┌──────────────┐
│  Vision      │
│  Analyst     │
│  (LLM)       │
└──────────────┘
```

### E. Reinforcement Learning Loop
Train a policy that maps screenshots → actions using reward feedback:
- Reward: action succeeded (window changed)
- Penalty: no change or wrong action
- Models: PPO, DQN with CNN encoder
- Simulated environments: `gymnasium` + virtual display (`xvfb`)

---

## 4. Practical Use Cases

| Use Case | Best Approach |
|----------|--------------|
| Web automation | Playwright/Selenium (no screenshot needed) |
| Desktop app automation | Your Screenshot→AI→xdotool loop |
| Form filling | OCR + click (fast, deterministic) |
| Repetitive mouse work | Template matching (fastest) |
| Accessibility testing | at-spi2 / AXTree |
| Game automation | Memory scanning / input simulation |
| GUI regression testing | Image diff + semantic segmentation |
| Legacy app support | Screenshot→AI→xdotool (only option) |

---

## 5. Limitations & Gotchas

- **Wayland:** xdotool doesn't work on Wayland without XWayland. Use `ydotool` or `wtype` as alternatives.
- **Multi-monitor:** Need to handle coordinate offsets between screens.
- **Popups/Tooltips:** Ephemeral UI elements disappear before you can click them.
- **AI cost:** Each loop iteration costs $0.01-0.10 with GPT-4o. Use state tracking to avoid redundant calls.
- **Latency:** ~2-5 seconds per iteration (screenshot + AI inference + click).
- **Rate limits:** Some AI providers limit image inputs per minute.

---

## 6. Quick Start

```bash
# Install dependencies
pip install openai pillow opencv-python
sudo apt install xdotool scrot

# Run
export OPENAI_API_KEY='sk-...'
python screen_controller.py "click on the login button"

# Or use local Ollama for free inference:
python screen_controller.py "type hello" \
  --model llama3.2-vision \
  --base-url http://localhost:11434/v1
```
