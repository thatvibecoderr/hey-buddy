"""
Hey Buddy - a task reminder for the desktop.

- Console window: add tasks with a date and time, see the list, delete tasks.
- At the due time, a little 3D-style figure walks in from the right edge of
  the screen, stops, waves, and a speech bubble above its head asks
  "Is this done?" with Yes / No buttons.
  - Yes  -> task is marked done.
  - No   -> it asks again in SNOOZE_MINUTES.
  After the answer the figure walks back off the screen.

Requires Python 3.8+ (tkinter is included with the standard Windows/macOS installers).
Run:  python task_reminder.py
"""

import calendar
import datetime as dt
import json
import math
import os
import queue
import shutil
import socket
import sys
import threading
import time
import uuid
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

APP_NAME = "Hey Buddy"
# Pictures, fonts and sounds ship with the app and are only read: next to this file, or, in the
# PyInstaller build, in the folder it unpacks them to.
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
# Tasks and settings belong to the user, and must survive reinstalls and updates.
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
DATA_FILE = os.path.join(DATA_DIR, "tasks.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")     # water reminder, chosen avatar, ...


def prepare_data_dir():
    """Creates DATA_DIR. The first time, brings over the tasks and settings an older version
    kept next to the program (the originals are left where they are)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    for path in (DATA_FILE, SETTINGS_FILE):
        old = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(path))
        if not os.path.exists(path) and os.path.exists(old):
            shutil.copy2(old, path)


# ---------- one copy at a time ----------

PORT_FILE = os.path.join(DATA_DIR, "instance.port")   # where the running copy listens
_instance_mutex = None


def claim_single_instance():
    """True if no other Hey Buddy is running. Otherwise asks the running one to show its window
    and returns False. Windows only; elsewhere every copy just runs."""
    global _instance_mutex
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _instance_mutex = kernel32.CreateMutexW(None, False, "HeyBuddy.SingleInstance")
        if ctypes.get_last_error() != 183:      # ERROR_ALREADY_EXISTS
            return True
    except (AttributeError, OSError):
        return True
    try:
        with open(PORT_FILE, encoding="utf-8") as f:
            port = int(f.read())
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.sendall(b"show")
    except (OSError, ValueError):
        pass
    return False


# ---------- start with Windows ----------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
BACKGROUND_ARG = "--background"     # started at login: go straight to the tray


def startup_command():
    if getattr(sys, "frozen", False):           # the installed Hey Buddy.exe
        return f'"{sys.executable}" {BACKGROUND_ARG}'
    exe = sys.executable                        # running from source: no console window
    windowless = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(windowless):
        exe = windowless
    return f'"{exe}" "{os.path.abspath(__file__)}" {BACKGROUND_ARG}'


def starts_with_windows():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except (ImportError, OSError):
        return False


def set_start_with_windows(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass

SNOOZE_MINUTES = 5          # how long "No" waits before asking again
BOTTOM_MARGIN = 48          # assumed taskbar height when the real one can't be asked for


def taskbar_top(screen_h):
    """Bottom edge of the usable screen area, i.e. the top of a bottom taskbar
    (the screen bottom if the taskbar is elsewhere or auto-hides)."""
    try:
        import ctypes
        import ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        SPI_GETWORKAREA = 0x30
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return rect.bottom
    except (AttributeError, OSError):
        pass
    return screen_h - BOTTOM_MARGIN
FIGURE_DIR = os.path.join(APP_DIR, "figure")   # PNG frames of the character
FONT_DIR = os.path.join(APP_DIR, "fonts")      # bundled Titillium Web .ttf files
UI_DIR = os.path.join(APP_DIR, "ui")           # rounded-corner artwork for the console
SOUND_DIR = os.path.join(APP_DIR, "sounds")    # WAVs made by tools/make_sounds.py
APP_ICON = os.path.join(UI_DIR, "app.ico")     # window, taskbar and tray icon (make_ui_assets.py)
POPUP_W, POPUP_H = 300, 410
GROUND_Y = 398              # where the figure's feet touch, inside the popup
WALK_SPEED = 4              # pixels per frame while walking (matches the character's stride)
FRAME_MS = 33
TRANSPARENT_KEY = "#01FE02" # colour made see-through on Windows
MAX_BUBBLE_CHARS = 100


# ---------- font ----------

def load_font():
    """Makes the bundled Titillium Web usable by this app without installing it.

    Returns the family name to use; falls back to Segoe UI if the files can't be loaded.
    """
    try:
        import ctypes
        files = [f for f in os.listdir(FONT_DIR) if f.lower().endswith(".ttf")]
        FR_PRIVATE = 0x10       # visible to this process only
        loaded = sum(ctypes.windll.gdi32.AddFontResourceExW(os.path.join(FONT_DIR, f), FR_PRIVATE, 0)
                     for f in files)
        return "Titillium Web" if loaded else "Segoe UI"
    except (OSError, AttributeError):
        return "Segoe UI"


FONT_FAMILY = load_font()


# ---------- storage ----------

def load_tasks():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def save_tasks(tasks):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2)
    os.replace(tmp, DATA_FILE)


DEFAULT_SETTINGS = {"water_on": False, "water_minutes": 120, "avatar": "buddy", "theme": "light",
                    "sound_on": True, "tray_tip_shown": False}

THEME_LIGHT = {
    "bg": "#F3F1FB",
    "card": "#FFFFFF",
    "pill": "#FFFFFF",
    "box": "#F6F5FB",
    "btn": "#4F46E5",
    "text": "#1E1B3A",
    "muted": "#6B6885",
    "faint": "#9794AE",
    "accent": "#4F46E5",
    "accent_soft": "#ECEBFF",
    "border": "#E8E5F4",
    "line": "#E4E2F0",
    "danger": "#D14343",
    "soft": "#F0EEF8",
    "switch_off": "#D5D2E6",
    "chip": "#FFFFFF",
    "badge_soft": "#ECEAF8",
    "badge_amber": "#FDEFC3",
    "badge_red": "#FEE2E2",
    "card_hover": "#F5F3FF",
    "past_day": "#CFCDE0",
    "accent_press": "#4338CA",
    "accent_soft_press": "#DDDBFF",
    "badge_soft_text": "#5B5878",
    "badge_amber_text": "#92400E",
    "badge_red_text": "#991B1B",
}

THEME_DARK = {
    "bg": "#121826",
    "card": "#1B2337",
    "pill": "#1B2337",
    "box": "#202B3D",
    "btn": "#8B7CFF",
    "text": "#F3F6FF",
    "muted": "#A7B3D0",
    "faint": "#7C8AA6",
    "accent": "#8B7CFF",
    "accent_soft": "#2A2D4B",
    "border": "#303B52",
    "line": "#35415A",
    "danger": "#FF7A7A",
    "soft": "#1A2030",
    "switch_off": "#46536E",
    "chip": "#1B2337",
    "badge_soft": "#2B3550",
    "badge_amber": "#6C4B22",
    "badge_red": "#6C2A3D",
    "card_hover": "#232D40",
    "past_day": "#4A5570",
    "accent_press": "#7465F0",
    "accent_soft_press": "#363A63",
    "badge_soft_text": "#C9D2EA",
    "badge_amber_text": "#FCD9A0",
    "badge_red_text": "#FFC4CF",
}


def _palette_for_theme(theme_name):
    theme_name = theme_name or "light"
    return THEME_DARK if theme_name == "dark" else THEME_LIGHT


def theme_ui_dir(theme_name):
    """Where the rounded-corner artwork for a theme lives: UI_DIR, or UI_DIR/dark."""
    return os.path.join(UI_DIR, "dark") if theme_name == "dark" else UI_DIR


def apply_theme(theme_name=None):
    palette = _palette_for_theme(theme_name)
    globals().update({
        "BG": palette["bg"],
        "CARD": palette["card"],
        "PILL": palette["pill"],
        "BOX": palette["box"],
        "BTN": palette["btn"],
        "TEXT": palette["text"],
        "MUTED": palette["muted"],
        "FAINT": palette["faint"],
        "ACCENT": palette["accent"],
        "ACCENT_SOFT": palette["accent_soft"],
        "BORDER": palette["border"],
        "LINE": palette["line"],
        "DANGER": palette["danger"],
        "SOFT": palette["soft"],
        "SWITCH_OFF": palette["switch_off"],
        "CHIP": palette["chip"],
        "BADGE_SOFT": palette["badge_soft"],
        "BADGE_AMBER": palette["badge_amber"],
        "BADGE_RED": palette["badge_red"],
        "CARD_HOVER": palette["card_hover"],
        "PAST_DAY": palette["past_day"],
        "ACCENT_PRESS": palette["accent_press"],
        "ACCENT_SOFT_PRESS": palette["accent_soft_press"],
        "BADGE_SOFT_TEXT": palette["badge_soft_text"],
        "BADGE_AMBER_TEXT": palette["badge_amber_text"],
        "BADGE_RED_TEXT": palette["badge_red_text"],
    })
    global STYLES
    STYLES = {
        "card": (16, CARD, BORDER, 1),
        "chip": (19, CARD, BORDER, 1),
        "chip_on": (19, ACCENT_SOFT, ACCENT, 1),
        "box": (10, BOX, BORDER, 1),
        "btn": (12, ACCENT, ACCENT, 0),
        "pill": (17, PILL, BORDER, 1),
        "badge_amber": (13, BADGE_AMBER, BADGE_AMBER, 0),
        "badge_soft": (13, BADGE_SOFT, BADGE_SOFT, 0),
        "badge_red": (13, BADGE_RED, BADGE_RED, 0),
        "check_off": (12, CARD, ACCENT, 2),
        "check_on": (12, GREEN, GREEN, 0),
        "switch_on": (12, ACCENT, ACCENT, 0),
        "switch_off": (12, SWITCH_OFF, SWITCH_OFF, 0),
        "knob": (9, CARD, CARD, 0),
        "step": (14, BOX, BORDER, 1),
    }


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        settings.update({k: saved[k] for k in DEFAULT_SETTINGS if k in saved})
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    if settings.get("theme") not in {"light", "dark"}:
        settings["theme"] = "light"
    return settings


def save_settings(settings):
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, SETTINGS_FILE)


# ---------- showing due times ----------

def format_due(due, now=None):
    """'Today, 2:30 PM' / 'Tomorrow, 9:00 AM' / 'Fri 16 Oct, 6:00 PM'."""
    now = now or dt.datetime.now()
    clock = due.strftime("%I:%M %p").lstrip("0")
    days = (due.date() - now.date()).days
    if days == 0:
        day = "Today"
    elif days == 1:
        day = "Tomorrow"
    elif days == -1:
        day = "Yesterday"
    else:
        day = due.strftime("%a %d %b")
        if due.year != now.year:
            day += f" {due.year}"
    return f"{day}, {clock}"


# ---------- sound ----------

def play_sound(name):
    """Plays SOUND_DIR/<name>.wav in the background, replacing whatever was playing.
    Silent if there is no such file or no sound support (winsound is Windows only)."""
    path = os.path.join(SOUND_DIR, name + ".wav")
    if not name or not os.path.exists(path):
        return
    try:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except (ImportError, RuntimeError):
        pass


def stop_sound():
    try:
        import winsound
        winsound.PlaySound(None, 0)
    except (ImportError, RuntimeError):
        pass


# ---------- the walking figure with its speech bubble ----------

class ReminderPopup(tk.Toplevel):
    SKIN, SKIN_DARK, SKIN_LIGHT = "#FFD8A8", "#E0A46C", "#FFF3E0"
    SHIRT, SHIRT_DARK, SHIRT_LIGHT = "#5C7CFA", "#3B5BDB", "#A5B8FF"
    PANTS, PANTS_DARK = "#455A64", "#263238"
    HAIR, SHOE = "#4E342E", "#212121"

    def __init__(self, master, text, on_answer, heading="Hey buddy! Is this done?",
                 avatar=None, sound=True):
        super().__init__(master)
        self.withdraw()           # set the window up fully before Windows first shows it
        self.av = avatar_info(avatar)
        self.sound = sound
        self.on_answer = on_answer
        self.task_text = text
        self.heading = heading
        self.tick_n = 0
        self.mode = "in"          # "in" (walking on) -> "idle" (asking) -> "out" (walking off)
        self.done = False
        self.btns = None

        self.overrideredirect(True)            # no title bar
        self.attributes("-topmost", True)      # stay above other windows
        try:
            # only the figure and the bubble are visible, not the window itself
            self.attributes("-transparentcolor", TRANSPARENT_KEY)
            bg = TRANSPARENT_KEY
        except tk.TclError:
            bg = "#FFFFFF"
        self.configure(bg=bg)

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.final_x = sw - POPUP_W - 20
        self.final_y = taskbar_top(sh) - GROUND_Y       # feet right on top of the taskbar
        self.screen_w = sw
        self.canvas = tk.Canvas(self, width=POPUP_W, height=POPUP_H, bg=bg, highlightthickness=0)
        self.canvas.pack()

        self.sprites = self.load_sprites()
        if not self.sprites:                    # the drawn fallback figure walks in from the right
            self.av = dict(self.av, enter="right", speed=WALK_SPEED)
        self.speed = self.av["speed"]
        self.pos_x = -POPUP_W if self.av["enter"] == "left" else sw
        self.geometry(f"{POPUP_W}x{POPUP_H}+{self.pos_x}+{self.final_y}")
        self.phase_start = 0                    # tick when the current mode began

        self.hwnd = self.never_take_focus()
        self.deiconify()
        if self.sound:
            play_sound(self.av["sound_in"])
        self.animate()

    def never_take_focus(self):
        """Windows only: the popup must not grab the keyboard from whatever the user is typing in
        (a stray Space or Enter would press "Yes, done"). Returns the window handle, or None."""
        try:
            import ctypes
            self.update_idletasks()
            hwnd = int(self.wm_frame(), 16)
            user32 = ctypes.windll.user32
            GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
            return hwnd
        except (AttributeError, OSError, ValueError, tk.TclError):
            return None

    def stay_visible(self):
        """Undoes anything that hid the popup: "Show desktop" (Win+D) minimizing it, or another
        always-on-top window that appeared later covering it."""
        if self.state() != "normal" or not self.winfo_ismapped():
            self.deiconify()
        if self.hwnd:
            import ctypes
            HWND_TOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = -1, 0x1, 0x2, 0x10
            ctypes.windll.user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                              SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        else:
            self.lift()

    def load_sprites(self):
        """Returns {name: PhotoImage}, or None to fall back to the drawn figure."""
        av = self.av
        try:
            return {name: tk.PhotoImage(master=self, file=os.path.join(av["dir"], name + ".png"))
                    for name in set(av["move_in"] + av["move_out"] + av["arrive"] + av["idle"])}
        except tk.TclError:
            return None

    # --- animation loop ---

    def animate(self):
        self.tick_n += 1
        if self.tick_n % 15 == 0:               # about twice a second
            self.stay_visible()
        if self.mode == "in":
            if self.av["enter"] == "left":
                self.pos_x = min(self.final_x, self.pos_x + self.speed)
            else:
                self.pos_x = max(self.final_x, self.pos_x - self.speed)
            self.geometry(f"+{self.pos_x}+{self.final_y}")
            if self.pos_x == self.final_x:
                self.mode = "idle"
                self.phase_start = self.tick_n
                if self.sound and self.av["sound_arrive"]:
                    play_sound(self.av["sound_arrive"])
                self.show_bubble()
        elif self.mode == "out":
            self.pos_x += self.speed
            self.geometry(f"+{self.pos_x}+{self.final_y}")
            if self.pos_x >= self.screen_w:
                self.destroy()
                self.on_answer(self.done)
                return
        self.draw()
        self.after(FRAME_MS, self.animate)

    # --- speech bubble ---

    def show_bubble(self):
        c = self.canvas
        cx = POPUP_W // 2
        x1, y1, x2, y2, r = 8, 6, POPUP_W - 8, 152, 18
        outline = "#90A4AE"
        pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1,
               x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2,
               x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2,
               x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
        c.create_polygon(pts, smooth=True, fill="#FFFFFF", outline=outline, width=2, tags="bubble")
        # tail pointing down at the head
        c.create_polygon(cx - 10, y2, cx + 16, y2, cx + 4, y2 + 28,
                         fill="#FFFFFF", outline=outline, width=2, tags="bubble")
        c.create_line(cx - 8, y2, cx + 14, y2, fill="#FFFFFF", width=4, tags="bubble")

        text = self.task_text
        if len(text) > MAX_BUBBLE_CHARS:
            text = text[:MAX_BUBBLE_CHARS - 1].rstrip() + "\u2026"
        c.create_text(x1 + 16, y1 + 12, anchor="nw", text=self.heading,
                      font=(FONT_FAMILY, 13, "bold"), fill="#263238", tags="bubble")
        c.create_text(x1 + 16, y1 + 38, anchor="nw", text=text, width=x2 - x1 - 32,
                      font=(FONT_FAMILY, 11), fill="#455A64", tags="bubble")

        self.btns = tk.Frame(c, bg="#FFFFFF")
        tk.Button(self.btns, text="Yes, done", width=10, bg="#43A047", fg="white",
                  activebackground="#2E7D32", activeforeground="white", relief="flat",
                  font=(FONT_FAMILY, 10, "bold"), command=lambda: self.answer(True)
                  ).pack(side="left", padx=(0, 6))
        tk.Button(self.btns, text="No, later", width=10, bg="#ECEFF1", fg="#263238",
                  activebackground="#CFD8DC", relief="flat", font=(FONT_FAMILY, 10),
                  command=lambda: self.answer(False)).pack(side="left")
        c.create_window(x1 + 16, y2 - 10, anchor="sw", window=self.btns, tags="bubble")
        self.lift()

    def answer(self, done):
        if self.mode != "idle":
            return
        self.done = done
        self.mode = "out"
        self.phase_start = self.tick_n
        self.canvas.delete("bubble")
        self.btns.destroy()

    # --- drawing the figure ---

    def ball(self, x, y, r, base, dark, light):
        """A shaded sphere: dark rim at lower right, highlight at upper left."""
        c = self.canvas
        c.create_oval(x - r, y - r, x + r, y + r, fill=dark, outline="", tags="fig")
        c.create_oval(x - r, y - r, x + r * 0.8, y + r * 0.8, fill=base, outline="", tags="fig")
        c.create_oval(x - r * 0.55, y - r * 0.6, x - r * 0.15, y - r * 0.25,
                      fill=light, outline="", tags="fig")

    def limb(self, x1, y1, x2, y2, width, base, dark):
        c = self.canvas
        c.create_line(x1, y1, x2, y2, width=width, fill=dark, capstyle="round", tags="fig")
        c.create_line(x1 - 1, y1, x2 - 1, y2 - 1, width=width - 4, fill=base,
                      capstyle="round", tags="fig")

    def draw_sprite(self):
        c = self.canvas
        t = self.tick_n
        cx = POPUP_W // 2
        av = self.av
        bob = 0     # the walk pictures already bob
        k = t - self.phase_start
        if self.mode == "in":
            name = av["move_in"][(k // av["ticks"]) % len(av["move_in"])]
        elif self.mode == "out":
            i = k // av["ticks"]
            seq = av["move_out"]
            name = seq[min(i, len(seq) - 1)] if av["out_once"] else seq[i % len(seq)]
        else:
            i = k // WAVE_TICKS
            if i < len(av["arrive"]):
                name = av["arrive"][i]
            else:
                name = av["idle"][(i - len(av["arrive"])) % len(av["idle"])]
        c.create_oval(cx - 34, GROUND_Y - 5, cx + 34, GROUND_Y + 3, fill="#5F6368",
                      outline="", tags="fig")
        c.create_image(cx, GROUND_Y - bob, anchor="s", image=self.sprites[name], tags="fig")

    def draw(self):
        c = self.canvas
        c.delete("fig")
        if self.sprites:
            self.draw_sprite()
            return
        t = self.tick_n
        walking = self.mode != "idle"
        d = {"in": -1, "idle": 0, "out": 1}[self.mode]   # direction of travel / gaze
        p = t * 0.45                                      # walk cycle phase
        bob = int(3 * abs(math.sin(p))) if walking else int(2 * math.sin(t * 0.15))

        cx = POPUP_W // 2
        hip_y = GROUND_Y - 40 - bob
        torso_top = hip_y - 54
        shoulder_y = torso_top + 14
        head_x, head_y, head_r = cx + d * 2, torso_top - 20, 26

        # soft shadow on the ground
        c.create_oval(cx - 34, GROUND_Y - 4, cx + 34, GROUND_Y + 4, fill="#5F6368",
                      outline="", tags="fig")

        def leg(hip_x, phase, base, dark):
            fx, fy = hip_x, GROUND_Y - 5
            if walking:
                fx += 14 * math.sin(phase)
                fy -= 7 * max(0, d * math.cos(phase))    # lift the foot on the forward swing
            self.limb(hip_x, hip_y, fx, fy, 11, base, dark)
            c.create_oval(fx - 9 + d * 3, fy - 5, fx + 9 + d * 3, fy + 5,
                          fill=self.SHOE, outline="", tags="fig")

        def arm(sx, sy, ex, ey, base, dark):
            self.limb(sx, sy, ex, ey, 10, base, dark)
            self.ball(ex, ey, 7, self.SKIN, self.SKIN_DARK, self.SKIN_LIGHT)

        def torso(half_w):
            c.create_oval(cx - half_w, torso_top, cx + half_w, hip_y + 6,
                          fill=self.SHIRT_DARK, outline="", tags="fig")
            c.create_oval(cx - half_w, torso_top, cx + half_w - 5, hip_y + 1,
                          fill=self.SHIRT, outline="", tags="fig")
            c.create_oval(cx - half_w + 7, torso_top + 8, cx - half_w + 17, torso_top + 24,
                          fill=self.SHIRT_LIGHT, outline="", tags="fig")

        if walking:
            # side-on: far limbs first and darker, then body, then near limbs
            arm(cx, shoulder_y, cx + 14 * math.sin(p), shoulder_y + 32,
                self.SHIRT_DARK, self.SHIRT_DARK)
            leg(cx + 3, p + math.pi, self.PANTS_DARK, self.PANTS_DARK)
            torso(21)
            leg(cx - 3, p, self.PANTS, self.PANTS_DARK)
        else:
            leg(cx - 9, 0, self.PANTS, self.PANTS_DARK)
            leg(cx + 9, 0, self.PANTS, self.PANTS_DARK)
            arm(cx - 22, shoulder_y, cx - 30, shoulder_y + 32, self.SHIRT, self.SHIRT_DARK)
            torso(26)

        # head, hair, face
        self.ball(head_x, head_y, head_r, self.SKIN, self.SKIN_DARK, self.SKIN_LIGHT)
        c.create_arc(head_x - head_r, head_y - head_r, head_x + head_r, head_y + head_r,
                     start=25 + d * 25, extent=130, style="chord",
                     fill=self.HAIR, outline="", tags="fig")
        face_x = head_x + d * 9
        for ex in (face_x - 8, face_x + 8):
            c.create_oval(ex - 3, head_y - 1, ex + 3, head_y + 5, fill="black", outline="", tags="fig")
            c.create_oval(ex - 2, head_y, ex, head_y + 2, fill="white", outline="", tags="fig")
        c.create_arc(face_x - 8, head_y + 4, face_x + 8, head_y + 16, start=200, extent=140,
                     style="arc", width=2, outline="black", tags="fig")
        if not walking:
            for ex in (face_x - 15, face_x + 15):
                c.create_oval(ex - 4, head_y + 7, ex + 4, head_y + 12,
                              fill="#FFB3A7", outline="", tags="fig")

        if walking:
            arm(cx, shoulder_y, cx + 14 * math.sin(p + math.pi), shoulder_y + 32,
                self.SHIRT, self.SHIRT_DARK)
        else:
            # waving arm
            ang = math.radians(-60 + 25 * math.sin(t * 0.35))
            sx, length = cx + 22, 34
            arm(sx, shoulder_y, sx + length * math.cos(ang), shoulder_y + length * math.sin(ang),
                self.SHIRT, self.SHIRT_DARK)


# ---------- the console window ----------

BG, CARD, PILL, BOX, BTN = "#F3F1FB", "#FFFFFF", "#FFFFFF", "#F6F5FB", "#4F46E5"
ACCENT, ACCENT_SOFT = "#4F46E5", "#ECEBFF"
TEXT, MUTED, FAINT = "#1E1B3A", "#6B6885", "#9794AE"
BORDER, LINE, DANGER, GREEN, AMBER = "#E7E5F4", "#E9E7F6", "#DC2626", "#16A34A", "#F59E0B"
SWITCH_OFF = "#D5D2E6"
BADGE_SOFT, BADGE_AMBER, BADGE_RED = "#ECEAF8", "#FDEFC3", "#FEE2E2"
CARD_HOVER = "#F5F3FF"
ICON_FONT = "Segoe MDL2 Assets"     # ships with Windows 10/11
ICON_BELL, ICON_CALENDAR, ICON_CLOCK = "\uEA8F", "\uE787", "\uE823"
ICON_TRASH, ICON_PLUS, ICON_CLOSE, ICON_CHECK = "\uE74D", "\uE710", "\uE711", "\uE73E"

# Rounded shapes are built from a pre-rendered, anti-aliased circle per style
# (UI_DIR/<name>.png, made by tools/make_ui_assets.py): name -> (radius, fill, border, border width)
STYLES = {
    "card": (16, CARD, BORDER, 1),
    "chip": (19, CARD, BORDER, 1),
    "chip_on": (19, ACCENT_SOFT, ACCENT, 1),
    "box": (10, BOX, BORDER, 1),
    "btn": (12, ACCENT, ACCENT, 0),
    "pill": (17, PILL, BORDER, 1),
    "badge_amber": (13, BADGE_AMBER, BADGE_AMBER, 0),
    "badge_soft": (13, BADGE_SOFT, BADGE_SOFT, 0),
    "badge_red": (13, BADGE_RED, BADGE_RED, 0),
    "check_off": (12, CARD, ACCENT, 2),
    "check_on": (12, GREEN, GREEN, 0),
    "switch_on": (12, ACCENT, ACCENT, 0),
    "switch_off": (12, SWITCH_OFF, SWITCH_OFF, 0),
    "knob": (9, CARD, CARD, 0),
    "step": (14, BOX, BORDER, 1),
}

apply_theme(DEFAULT_SETTINGS["theme"])

WHEN_CHIPS = (("15", "In 15 min"), ("60", "In 1 hour"), ("tomorrow", "Tomorrow 9 AM"),
              ("custom", "Pick a time\u2026"))
DEFAULT_WHEN = "60"         # today, one hour from now


# The characters that can deliver reminders. Each one's pictures live in its "dir":
#   enter     side of the screen it comes in from (it always leaves to the right)
#   speed     pixels per animation frame while moving
#   move_in / move_out   pictures while coming in / leaving, FRAME_MS * ticks each;
#             move_out plays once and holds its last picture when out_once is set
#   arrive    played once on arrival, then idle loops while the question is on screen
#   header    head-and-shoulders loop in the console header: UI_DIR/header_<key>_<frame>.png,
#             cut from the pictures by tools/make_ui_assets.py using header_crop
#             (left, top, right, bottom as fractions of a picture)
#   sound_in / sound_arrive   SOUND_DIR/<name>.wav played as it starts coming in / when it
#             stops (None: nothing), made by tools/make_sounds.py; each one cuts off the last
AVATARS = {
    "buddy": {
        "name": "Buddy", "dir": FIGURE_DIR, "enter": "right", "speed": WALK_SPEED, "ticks": 2,
        "move_in": [f"walk_in_{i}" for i in range(1, 7)],
        "move_out": [f"walk_out_{i}" for i in range(1, 7)], "out_once": False,
        "arrive": [],
        # raise the hand, wave it a few times, lower it, rest, repeat
        "idle": (["stand"] * 2 + ["wave_1", "wave_2"] + ["wave_3", "wave_4"] * 3
                 + ["wave_3", "wave_2", "wave_1"] + ["stand"] * 10),
        "header": (["stand"] * 22 + ["wave_1", "wave_2"] + ["wave_3", "wave_4"] * 3
                   + ["wave_3", "wave_2", "wave_1"]),
        "header_crop": (0, 0, 1, 0.46),
        "sound_in": "buddy_in", "sound_arrive": "arrive",       # footsteps, then a chime
    },
    "minnal": {
        "name": "Minnal", "dir": os.path.join(APP_DIR, "figure_minnal"),
        "enter": "left", "speed": 70, "ticks": 1,
        "move_in": [f"run_{i}" for i in range(1, 7)],
        "move_out": ["exit_1", "exit_2", "exit_3"], "out_once": True,
        "arrive": [],
        # two slightly different raised-hand pictures, alternated, make the wave
        "idle": ["stand"] * 2 + ["wave", "wave2"] * 4 + ["stand2"] * 2 + ["stand"] * 8,
        "header": ["stand"] * 22 + ["wave", "wave2"] * 4,
        "header_crop": (0.22, 0, 0.8, 0.42),
        # he arrives in a blink, so one sound carries the whoosh, zap and chime
        "sound_in": "minnal_in", "sound_arrive": None,
    },
}
DEFAULT_AVATAR = "buddy"
WAVE_TICKS = 4              # animation frames per picture while waving
AVATAR_H = 62               # height of the header character on screen
AVATAR_MS = 130


def avatar_info(key):
    return AVATARS.get(key) or AVATARS[DEFAULT_AVATAR]

PREVIEW_ID = "preview"      # "Preview" in the avatar row: shows the reminder once, now
ICON_PERSON = "\uE77B"
ICON_VOLUME, ICON_MUTE = "\uE767", "\uE74F"
ICON_SETTINGS, ICON_BACK = "\uE713", "\uE72B"
ICON_SUN, ICON_MOON = "\uE706", "\uE708"
ICON_POWER = "\uE7E8"
WATER_ID = "water"          # stands in for a task id in the reminder queue
WATER_STEP, WATER_MIN, WATER_MAX = 30, 30, 12 * 60     # minutes
WATER_BLUE = "#0EA5E9"
ICON_DROP = "\uEB42"


def duration_text(minutes):
    hours, rest = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours} hr" if hours else "", f"{rest} min" if rest else "") if part)


def clock_text(when):
    return when.strftime("%I:%M %p").lstrip("0")


def relative_text(when, now):
    """'in 15 min' / 'in 2 hr 5 min' / 'tomorrow' / 'in 3 days'; None once the time has passed."""
    secs = (when - now).total_seconds()
    if secs <= 0:
        return None
    mins = int(secs // 60)
    if mins < 1:
        return "in under a minute"
    if mins < 60:
        return f"in {mins} min"
    if mins < 24 * 60:
        hours, rest = divmod(mins, 60)
        return f"in {hours} hr" + (f" {rest} min" if rest else "")
    days = (when.date() - now.date()).days
    return "tomorrow" if days == 1 else f"in {days} days"


class Dropdown(tk.Toplevel):
    """A borderless panel under a field; closes when the focus goes elsewhere."""
    last_closed = 0.0

    def __init__(self, master, x, y):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BORDER, padx=1, pady=1)
        self.body = tk.Frame(self, bg=CARD, padx=12, pady=12)
        self.body.pack()
        self.geometry(f"+{x}+{y}")
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<FocusOut>", lambda e: self.after(1, self.check_focus))
        self.focus_force()

    @classmethod
    def just_closed(cls):
        # the click that took the focus away (and closed it) should not reopen it
        return time.monotonic() - cls.last_closed < 0.25

    def check_focus(self):
        if not self.winfo_exists():
            return
        try:
            focus = self.focus_get()
        except KeyError:        # focus is inside a ttk spinbox's internal popdown
            return
        if focus is None or not str(focus).startswith(str(self)):
            self.close()

    def close(self):
        if self.winfo_exists():
            Dropdown.last_closed = time.monotonic()
            self.destroy()


class CalendarPopup(Dropdown):
    def __init__(self, master, x, y, date, on_pick):
        super().__init__(master, x, y)
        self.date = date
        self.on_pick = on_pick
        self.view = date.replace(day=1)
        self.render()

    def shift(self, months):
        month = self.view.month - 1 + months
        self.view = self.view.replace(year=self.view.year + month // 12, month=month % 12 + 1)
        self.render()

    def pick(self, date):
        self.close()
        self.on_pick(date)

    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        today = dt.date.today()
        font, bold = (FONT_FAMILY, 10), (FONT_FAMILY, 10, "bold")

        def nav(col, symbol, months, enabled):
            lbl = tk.Label(self.body, text=symbol, font=(FONT_FAMILY, 13, "bold"), bg=CARD,
                           fg=ACCENT if enabled else BORDER)
            lbl.grid(row=0, column=col)
            if enabled:
                lbl.config(cursor="hand2")
                lbl.bind("<Button-1>", lambda e: self.shift(months))

        nav(0, "\u2039", -1, self.view > today.replace(day=1))   # no going back before this month
        tk.Label(self.body, text=self.view.strftime("%B %Y"), font=bold, bg=CARD,
                 fg=TEXT).grid(row=0, column=1, columnspan=5, pady=(0, 6))
        nav(6, "\u203A", 1, True)

        for col, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            tk.Label(self.body, text=name, font=(FONT_FAMILY, 9), bg=CARD, fg=MUTED,
                     width=4).grid(row=1, column=col)

        weeks = calendar.monthcalendar(self.view.year, self.view.month)
        for r, week in enumerate(weeks, start=2):
            for col, day in enumerate(week):
                if not day:
                    continue
                date = self.view.replace(day=day)
                cell = tk.Label(self.body, text=day, font=font, bg=CARD, fg=TEXT, width=4, pady=4)
                cell.grid(row=r, column=col, padx=1, pady=1)
                if date < today:
                    cell.config(fg=PAST_DAY)
                    continue
                if date == self.date:
                    cell.config(bg=ACCENT, fg="white", font=bold)
                else:
                    if date == today:
                        cell.config(fg=ACCENT, font=bold)
                    cell.bind("<Enter>", lambda e, w=cell: w.config(bg=ACCENT_SOFT))
                    cell.bind("<Leave>", lambda e, w=cell: w.config(bg=CARD))
                cell.config(cursor="hand2")
                cell.bind("<Button-1>", lambda e, d=date: self.pick(d))


class TimePopup(Dropdown):
    def __init__(self, master, x, y, when, on_pick):
        super().__init__(master, x, y)
        self.on_pick = on_pick
        self.hour_var = tk.StringVar(value=str(when.hour % 12 or 12))
        self.min_var = tk.StringVar(value=f"{when.minute:02d}")
        big = (FONT_FAMILY, 12)
        hour = ttk.Spinbox(self.body, textvariable=self.hour_var, from_=1, to=12, wrap=True,
                           width=3, justify="center", font=big)
        hour.pack(side="left")
        tk.Label(self.body, text=":", font=(FONT_FAMILY, 12, "bold"), bg=CARD, fg=TEXT
                 ).pack(side="left", padx=3)
        minute = ttk.Spinbox(self.body, textvariable=self.min_var, from_=0, to=59, wrap=True,
                             width=3, format="%02.0f", justify="center", font=big)
        minute.pack(side="left")
        self.ampm = tk.Button(self.body, text="AM" if when.hour < 12 else "PM", width=4,
                              relief="flat", bd=0, bg=ACCENT_SOFT, fg=ACCENT,
                              activebackground=ACCENT_SOFT_PRESS, activeforeground=ACCENT,
                              font=(FONT_FAMILY, 10, "bold"), cursor="hand2", command=self.toggle_ampm)
        self.ampm.pack(side="left", padx=(8, 0), ipady=1)
        tk.Button(self.body, text="Set", relief="flat", bd=0, bg=ACCENT, fg="white",
                  activebackground=ACCENT_PRESS, activeforeground="white",
                  font=(FONT_FAMILY, 10, "bold"), padx=14, cursor="hand2", command=self.submit
                  ).pack(side="left", padx=(10, 0), ipady=1)
        for w in (hour, minute):
            w.bind("<Return>", lambda e: self.submit())

    def toggle_ampm(self):
        self.ampm.config(text="PM" if self.ampm["text"] == "AM" else "AM")

    def submit(self):
        try:
            hour, minute = int(self.hour_var.get()), int(self.min_var.get())
        except ValueError:
            return
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            return
        hour = hour % 12 + (12 if self.ampm["text"] == "PM" else 0)
        self.close()
        self.on_pick(dt.time(hour, minute))


class ConsoleApp:
    def __init__(self, root, background=False):
        self.root = root
        prepare_data_dir()
        self.tasks = load_tasks()
        self.queued = []          # task ids waiting to be shown
        self.current_id = None    # task id whose popup is on screen
        self.popup = None

        self.when_mode = DEFAULT_WHEN
        self.custom_when = None   # used when when_mode == "custom"
        self.errors = {}          # "task" / "when" -> message shown under that field
        self.hits = []            # clickable areas: (x1, y1, x2, y2, callback)
        self.page = "main"        # "main" (reminders) or "settings"
        self.boxes = {}           # where the date / time fields were last drawn
        self.shown_minute = None

        self.settings = load_settings()
        apply_theme(self.settings.get("theme", "light"))
        self.water_next = None    # when the next water reminder is due (not saved: restarts from now)
        self.schedule_water()

        root.title(APP_NAME)
        root.geometry("940x780")
        root.minsize(640, 560)
        root.configure(bg=BG)
        try:
            root.iconbitmap(default=APP_ICON)
        except tk.TclError:
            pass

        def font(size, weight="normal", family=FONT_FAMILY, **kw):
            return tkfont.Font(root=root, family=family, size=size, weight=weight, **kw)

        self.f_title, self.f_h2, self.f_h3 = font(20, "bold"), font(14, "bold"), font(11, "bold")
        self.f_text, self.f_bold = font(10), font(10, "bold")
        self.f_small, self.f_small_bold, self.f_label = font(9), font(9, "bold"), font(9, "bold")
        self.f_entry, self.f_box, self.f_btn = font(14), font(11), font(11, "bold")
        self.f_row, self.f_done = font(12), font(11, overstrike=1)
        self.f_icon, self.f_icon_big = font(11, family=ICON_FONT), font(16, family=ICON_FONT)
        self.f_icon_small, self.f_dot = font(8, family=ICON_FONT), font(7, family="Segoe UI")

        self.images = self.load_images()

        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.placeholder = "e.g. stretch and drink some water"
        self.placeholder_on = False
        self.task_entry = tk.Entry(self.canvas, font=self.f_entry, relief="flat", bd=0, bg=CARD,
                                   fg=TEXT, insertbackground=TEXT, highlightthickness=0)
        self.entry_win = self.canvas.create_window(0, 0, anchor="nw", window=self.task_entry, height=36)
        self.task_entry.bind("<Return>", lambda e: self.add_task())
        self.task_entry.bind("<KeyRelease>", lambda e: self.clear_error("task"))
        self.task_entry.bind("<FocusIn>", lambda e: self.entry_focus(True))
        self.task_entry.bind("<FocusOut>", lambda e: self.entry_focus(False))
        self.entry_focus(False)

        self.avatar = self.load_avatar()
        self.avatar_tick = 0
        self.avatar_item = self.canvas.create_image(0, 0, anchor="nw")
        self.animate_avatar()

        self.canvas.bind("<Configure>", lambda e: self.render())
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Motion>", self.on_motion)
        root.bind_all("<MouseWheel>", self.on_wheel)

        # The tray icon and a second copy of the app talk to this one from their own threads;
        # Tk may only be used from this one, so they leave "show" / "quit" here instead.
        self.requests = queue.Queue()
        self.tray = self.start_tray()
        self.listen_for_show()
        root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.poll_requests()

        if background and self.tray:
            root.withdraw()
        self.render()
        self.tick()

    # --- tray and window ---

    def start_tray(self):
        """The icon by the clock: click to open the window, right-click for Open / Quit.
        None if pystray or Pillow is missing; closing the window then quits, as before."""
        try:
            import pystray
            from PIL import Image
            image = Image.open(APP_ICON)
        except (ImportError, OSError):
            return None
        menu = pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", lambda icon, item: self.requests.put("show"),
                             default=True),
            pystray.MenuItem("Quit", lambda icon, item: self.requests.put("quit")))
        tray = pystray.Icon("HeyBuddy", image, APP_NAME, menu)
        tray.run_detached()
        return tray

    def listen_for_show(self):
        """Lets a second copy, started from a shortcut, bring this window up instead."""
        try:
            server = socket.create_server(("127.0.0.1", 0))
            with open(PORT_FILE, "w", encoding="utf-8") as f:
                f.write(str(server.getsockname()[1]))
        except OSError:
            return

        def serve():
            while True:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(2)
                    try:
                        if conn.recv(16) == b"show":
                            self.requests.put("show")
                    except OSError:
                        pass

        threading.Thread(target=serve, daemon=True).start()

    def poll_requests(self):
        while not self.requests.empty():
            request = self.requests.get()
            if request == "show":
                self.show_window()
            elif request == "quit":
                self.quit_app()
                return
        self.root.after(150, self.poll_requests)

    def show_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def hide_window(self):
        """The X button: reminders keep coming while the window is away."""
        if not self.tray:
            self.quit_app()
            return
        self.root.withdraw()
        if not self.settings["tray_tip_shown"]:
            self.tray.notify("Still here, and your reminders will still come. "
                             "Right-click my icon by the clock to quit.", APP_NAME)
            self.settings["tray_tip_shown"] = True
            save_settings(self.settings)

    def quit_app(self):
        stop_sound()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    # --- drawing helpers ---

    def load_avatar(self):
        """The chosen avatar's header pictures: frame name -> PhotoImage;
        None (the bell icon is shown instead) if a file is missing."""
        key = self.settings["avatar"] if self.settings["avatar"] in AVATARS else DEFAULT_AVATAR
        try:
            return {name: tk.PhotoImage(master=self.root,
                                        file=os.path.join(UI_DIR, f"header_{key}_{name}.png"))
                    for name in set(AVATARS[key]["header"])}
        except tk.TclError:
            return None

    def animate_avatar(self):
        if self.avatar:
            seq = avatar_info(self.settings["avatar"])["header"]
            name = seq[self.avatar_tick % len(seq)]
            self.canvas.itemconfigure(self.avatar_item, image=self.avatar[name])
        else:
            self.canvas.itemconfigure(self.avatar_item, image="")
        self.avatar_tick += 1
        self.root.after(AVATAR_MS, self.animate_avatar)

    def choose_avatar(self, key):
        if key != self.settings["avatar"]:
            self.settings["avatar"] = key
            save_settings(self.settings)
            self.avatar = self.load_avatar()
            self.avatar_tick = 0
            self.render()

    def preview_avatar(self):
        if PREVIEW_ID not in self.queued and self.current_id != PREVIEW_ID:
            self.queued.append(PREVIEW_ID)
            self.show_next()

    def load_images(self):
        """name -> (whole circle, [four corner quarters]); None if the file is missing."""
        images = {}
        folder = theme_ui_dir(self.settings.get("theme"))
        for name, (r, _fill, _border, _bw) in STYLES.items():
            try:
                whole = tk.PhotoImage(master=self.root, file=os.path.join(folder, name + ".png"))
                quarters = []
                for qx, qy in ((0, 0), (r, 0), (0, r), (r, r)):
                    q = tk.PhotoImage(master=self.root)
                    q.tk.call(q, "copy", whole, "-from", qx, qy, qx + r, qy + r)
                    quarters.append(q)
                images[name] = (whole, quarters)
            except tk.TclError:
                images[name] = None
        return images

    def rounded(self, x1, y1, x2, y2, style):
        c = self.canvas
        r, fill, border, bw = STYLES[style]
        if not self.images[style]:      # no artwork: plain square box
            c.create_rectangle(x1, y1, x2 - 1, y2 - 1, fill=fill, outline=border, tags="ui")
            return
        for ax1, ay1, ax2, ay2 in ((x1 + r, y1, x2 - r, y2), (x1, y1 + r, x2, y2 - r)):
            if bw:
                c.create_rectangle(ax1, ay1, ax2, ay2, fill=border, outline="", tags="ui")
            ix, iy = (0, bw) if ax1 != x1 else (bw, 0)
            c.create_rectangle(ax1 + ix, ay1 + iy, ax2 - ix, ay2 - iy, fill=fill, outline="", tags="ui")
        tl, tr, bl, br = self.images[style][1]
        c.create_image(x1, y1, anchor="nw", image=tl, tags="ui")
        c.create_image(x2 - r, y1, anchor="nw", image=tr, tags="ui")
        c.create_image(x1, y2 - r, anchor="nw", image=bl, tags="ui")
        c.create_image(x2 - r, y2 - r, anchor="nw", image=br, tags="ui")

    def circle(self, cx, cy, style):
        r, fill, border, bw = STYLES[style]
        if self.images[style]:
            self.canvas.create_image(cx, cy, image=self.images[style][0], tags="ui")
        else:
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline=border,
                                    width=max(bw, 1), tags="ui")

    def text(self, x, y, text, font, fill, anchor="w"):
        return self.canvas.create_text(x, y, text=text, font=font, fill=fill, anchor=anchor, tags="ui")

    def hit(self, x1, y1, x2, y2, callback):
        self.hits.append((x1, y1, x2, y2, callback))

    @staticmethod
    def fit(text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "\u2026") > width:
            text = text[:-1]
        return text.rstrip() + "\u2026"

    # --- mouse ---

    def find_hit(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for x1, y1, x2, y2, callback in self.hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return callback
        return None

    def on_click(self, event):
        callback = self.find_hit(event)
        if callback:
            callback()
        else:
            self.canvas.focus_set()     # clicking empty space leaves the text field

    def on_motion(self, event):
        self.canvas.config(cursor="hand2" if self.find_hit(event) else "")

    def on_wheel(self, event):
        top, bottom = self.canvas.yview()
        if (top, bottom) != (0.0, 1.0):
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # --- the task text field ---

    def entry_focus(self, focused):
        if focused and self.placeholder_on:
            self.task_entry.delete(0, "end")
            self.task_entry.config(fg=TEXT)
            self.placeholder_on = False
        elif not focused and not self.task_entry.get():
            self.task_entry.insert(0, self.placeholder)
            self.task_entry.config(fg=FAINT)
            self.placeholder_on = True
        self.entry_focused = focused
        if hasattr(self, "shown_width"):
            self.render()

    def task_text(self):
        return "" if self.placeholder_on else self.task_entry.get().strip()

    def clear_error(self, field):
        """Drops a field's error message once the user starts fixing that field."""
        if field == "task" and not self.task_text():
            return
        if self.errors.pop(field, None):
            self.render()

    # --- when ---

    def current_when(self):
        now = dt.datetime.now()
        if self.when_mode == "custom":
            return self.custom_when
        if self.when_mode == "tomorrow":
            return dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(9, 0))
        when = now + dt.timedelta(minutes=15 if self.when_mode == "15" else 60)
        if when.second or when.microsecond:     # whole minutes, never earlier than asked
            when = when.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        return when

    def choose_when(self, mode):
        if mode == "custom":
            self.open_time()
            return
        self.when_mode = mode
        self.errors.pop("when", None)
        self.render()

    def make_custom(self):
        if self.when_mode != "custom":
            self.custom_when = self.current_when()
            self.when_mode = "custom"
            self.render()

    def screen_pos(self, box):
        x1, _y1, _x2, y2 = self.boxes[box]
        return (self.canvas.winfo_rootx() + int(x1 - self.canvas.canvasx(0)),
                self.canvas.winfo_rooty() + int(y2 - self.canvas.canvasy(0)) + 4)

    def open_calendar(self):
        if Dropdown.just_closed():
            return
        self.make_custom()
        CalendarPopup(self.root, *self.screen_pos("date"), self.custom_when.date(), self.set_date)

    def open_time(self):
        if Dropdown.just_closed():
            return
        self.make_custom()
        TimePopup(self.root, *self.screen_pos("time"), self.custom_when, self.set_time)

    def set_date(self, date):
        self.custom_when = dt.datetime.combine(date, self.custom_when.time())
        self.errors.pop("when", None)
        self.render()

    def set_time(self, clock):
        self.custom_when = dt.datetime.combine(self.custom_when.date(), clock)
        self.errors.pop("when", None)
        self.render()

    # --- drawing the whole window ---

    def render(self):
        c = self.canvas
        c.delete("ui")
        self.hits = []
        now = dt.datetime.now()
        self.shown_minute = now.minute
        width = max(c.winfo_width(), 400)
        self.shown_width = width
        col = min(880, width - 48)
        x0 = (width - col) // 2
        x1 = x0 + col
        pending = sorted((t for t in self.tasks if not t["done"]), key=lambda t: t["due"])
        done = sorted((t for t in self.tasks if t["done"]),
                      key=lambda t: t.get("done_at") or t["due"], reverse=True)

        # header
        y = 34
        if self.avatar:
            c.coords(self.avatar_item, x0 - 10, y - 10)
            tx = x0 - 10 + self.avatar["stand"].width() + 6
        else:
            self.rounded(x0, y, x0 + 48, y + 48, "btn")
            self.text(x0 + 24, y + 24, ICON_BELL, self.f_icon_big, "white", "center")
            tx = x0 + 62
        self.text(tx, y + 12, APP_NAME, self.f_title, TEXT)
        self.text(tx, y + 40, "Tell me what to remind you about. I'll walk over when it's time.",
                  self.f_text, MUTED)
        label = f"{len(pending)} pending"
        w = self.f_bold.measure(label) + 48
        self.rounded(x1 - w, y + 7, x1, y + 41, "pill")
        self.text(x1 - w + 18, y + 24, "\u25CF", self.f_dot, AMBER if pending else GREEN, "center")
        self.text(x1 - w + 30, y + 24, label, self.f_bold, TEXT)
        gx = x1 - w - 10 - 17                           # settings button, left of the pill
        in_settings = self.page == "settings"
        self.circle(gx, y + 24, "pill")
        self.text(gx, y + 24, ICON_SETTINGS, self.f_icon, ACCENT if in_settings else MUTED, "center")
        self.hit(gx - 17, y + 7, gx + 17, y + 41,
                 lambda: self.show_page("main" if in_settings else "settings"))
        y += 76

        if in_settings:
            c.itemconfigure(self.entry_win, state="hidden")
            y = self.render_settings(x0, x1, y)
            c.config(scrollregion=(0, 0, width, max(y + 28, c.winfo_height())))
            return
        c.itemconfigure(self.entry_win, state="normal")

        # new reminder card
        card_h = 270
        px = x0 + 24
        task_error, when_error = self.errors.get("task"), self.errors.get("when")
        self.rounded(x0, y, x1, y + card_h, "card")
        self.text(px, y + 30, "REMIND ME TO", self.f_label, MUTED)
        c.coords(self.entry_win, px, y + 48)
        c.itemconfigure(self.entry_win, width=col - 48)
        c.create_rectangle(px, y + 90, x1 - 24, y + 92, outline="", tags="ui",
                           fill=DANGER if task_error else
                           ACCENT if getattr(self, "entry_focused", False) else LINE)
        if task_error:
            self.text(px, y + 101, task_error, self.f_small, DANGER)

        cy = y + 110
        self.text(px, cy + 19, "When", self.f_text, MUTED)
        cx = px + self.f_text.measure("When") + 14
        for mode, label in WHEN_CHIPS:
            on = self.when_mode == mode
            w = self.f_bold.measure(label) + 34
            self.rounded(cx, cy, cx + w, cy + 38, "chip_on" if on else "chip")
            self.text(cx + w // 2, cy + 19, label, self.f_bold if on else self.f_text,
                      ACCENT if on else TEXT, "center")
            self.hit(cx, cy, cx + w, cy + 38, lambda mode=mode: self.choose_when(mode))
            cx += w + 10
        c.create_rectangle(px, y + 166, x1 - 24, y + 167, fill=SOFT, outline="", tags="ui")

        by = y + 186
        when = self.current_when()
        bx = px
        for box, icon, label, action in (
            ("date", ICON_CALENDAR, when.strftime("%a, ") + f"{when.day} " + when.strftime("%b"),
             self.open_calendar),
            ("time", ICON_CLOCK, clock_text(when), self.open_time),
        ):
            w = self.f_box.measure(label) + 58
            self.rounded(bx, by, bx + w, by + 44, "box")
            self.text(bx + 24, by + 22, icon, self.f_icon, MUTED, "center")
            self.text(bx + 42, by + 22, label, self.f_box, TEXT)
            self.hit(bx, by, bx + w, by + 44, action)
            self.boxes[box] = (bx, by, bx + w, by + 44)
            bx += w + 10
        hint = relative_text(when, now)
        self.text(bx + 2, by + 22, hint or "already passed", self.f_text, MUTED if hint else DANGER)
        if when_error:
            self.text(px, by + 58, when_error, self.f_small, DANGER)

        bw = 178
        self.rounded(x1 - 24 - bw, by - 2, x1 - 24, by + 46, "btn")
        self.text(x1 - 24 - bw + 34, by + 22, ICON_PLUS, self.f_icon, "white", "center")
        self.text(x1 - 24 - bw + 54, by + 21, "Add reminder", self.f_btn, "white")
        self.hit(x1 - 24 - bw, by - 2, x1 - 24, by + 46, self.add_task)
        y += card_h + 12

        # water reminder
        on = self.settings["water_on"]
        self.rounded(x0, y, x1, y + 68, "card")
        self.text(x0 + 36, y + 34, ICON_DROP, self.f_icon_big, WATER_BLUE if on else FAINT, "center")
        self.text(x0 + 72, y + 24, "Water reminder", self.f_row, TEXT)
        every = duration_text(self.settings["water_minutes"])
        self.text(x0 + 72, y + 47, f"Every {every} \u00B7 next at {clock_text(self.water_next)}"
                  if on else "Off", self.f_small, MUTED)

        sx = x1 - 24 - 44                               # on/off switch
        self.rounded(sx, y + 22, sx + 44, y + 46, "switch_on" if on else "switch_off")
        self.circle(sx + (32 if on else 12), y + 34, "knob")
        self.hit(sx - 6, y + 16, sx + 50, y + 52, self.toggle_water)

        plus_x = sx - 20 - 14                           # interval stepper: (-) 2 hr (+)
        minus_x = plus_x - 14 - 96 - 14
        for cx, symbol, step in ((minus_x, "\u2212", -WATER_STEP), (plus_x, "+", WATER_STEP)):
            self.circle(cx, y + 34, "step")
            self.text(cx, y + 33, symbol, self.f_h3, TEXT, "center")
            self.hit(cx - 16, y + 18, cx + 16, y + 50, lambda step=step: self.step_water(step))
        self.text((minus_x + plus_x) // 2, y + 34, every, self.f_bold, TEXT if on else MUTED, "center")
        y += 68 + 34

        # up next
        self.text(x0, y, "Up next", self.f_h2, TEXT)
        y += 28
        if not pending:
            self.text(x0, y + 12, "Nothing coming up. Add a reminder above.", self.f_text, MUTED)
            y += 40
        for task in pending:
            due = dt.datetime.fromisoformat(task["due"])
            self.rounded(x0, y, x1, y + 68, "card")
            self.circle(x0 + 36, y + 34, "check_off")
            self.hit(x0 + 18, y + 16, x0 + 54, y + 52, lambda task=task: self.mark_done(task))

            rel = relative_text(due, now)
            if rel is None:
                badge, style, colour = "due now", "badge_red", BADGE_RED_TEXT
            elif due - now < dt.timedelta(hours=1):
                badge, style, colour = rel, "badge_amber", BADGE_AMBER_TEXT
            else:
                badge, style, colour = rel, "badge_soft", BADGE_SOFT_TEXT
            w = self.f_small_bold.measure(badge) + 26
            right = x1 - 70
            self.rounded(right - w, y + 21, right, y + 47, style)
            self.text(right - w // 2, y + 34, badge, self.f_small_bold, colour, "center")

            self.text(x1 - 35, y + 34, ICON_TRASH, self.f_icon, MUTED, "center")
            self.hit(x1 - 52, y + 17, x1 - 18, y + 51, lambda task=task: self.delete_task(task))

            title = self.fit(task["text"], self.f_row, right - w - 16 - (x0 + 72))
            self.text(x0 + 72, y + 24, title, self.f_row, TEXT)
            day, clock = format_due(due, now).split(", ")
            self.text(x0 + 72, y + 47, f"{day} · {clock}", self.f_small, MUTED)
            y += 78

        # done
        def done_day(task):
            return dt.datetime.fromisoformat(task.get("done_at") or task["due"]).date()

        today = [t for t in done if done_day(t) == now.date()]
        earlier = [t for t in done if done_day(t) != now.date()]
        for heading, group in (("Done today", today), ("Earlier", earlier)):
            if not group:
                continue
            y += 18
            self.text(x0, y, f"{heading} · {len(group)}", self.f_h3, MUTED)
            y += 18
            for task in group:
                due = dt.datetime.fromisoformat(task["due"])
                self.circle(x0 + 36, y + 29, "check_on")
                self.text(x0 + 36, y + 29, ICON_CHECK, self.f_icon_small, "white", "center")
                stamp = clock_text(due) if due.date() == now.date() else format_due(due, now).split(", ")[0]
                self.text(x1 - 70, y + 29, stamp, self.f_text, MUTED, "e")
                title = self.fit(task["text"], self.f_done, x1 - 70 - self.f_text.measure(stamp) - 16 - (x0 + 72))
                self.text(x0 + 72, y + 29, title, self.f_done, FAINT)
                self.text(x1 - 35, y + 29, ICON_CLOSE, self.f_icon_small, MUTED, "center")
                self.hit(x1 - 52, y + 12, x1 - 18, y + 46, lambda task=task: self.remove_task(task))
                y += 58

        c.config(scrollregion=(0, 0, width, max(y + 28, c.winfo_height())))

    def render_settings(self, x0, x1, y):
        """The settings page, below the header; returns the y where it ends."""
        self.text(x0, y + 18, "Settings", self.f_h2, TEXT)
        w = self.f_bold.measure("Back") + 52           # back to the reminders
        self.rounded(x1 - w, y, x1, y + 38, "chip")
        self.text(x1 - w + 22, y + 19, ICON_BACK, self.f_icon_small, ACCENT, "center")
        self.text(x1 - w + 36, y + 19, "Back", self.f_bold, ACCENT)
        self.hit(x1 - w, y, x1, y + 38, lambda: self.show_page("main"))
        y += 56

        # appearance
        sx = x1 - 24 - 44                               # on/off switches
        self.rounded(x0, y, x1, y + 68, "card")
        self.text(x0 + 36, y + 34, ICON_MOON if self.settings.get("theme") == "dark" else ICON_SUN,
                  self.f_icon_big, ACCENT, "center")
        self.text(x0 + 72, y + 24, "Theme", self.f_row, TEXT)
        self.text(x0 + 72, y + 47, "Dark mode" if self.settings.get("theme") == "dark" else "Light mode",
                  self.f_small, MUTED)
        theme_is_dark = self.settings.get("theme") == "dark"
        self.rounded(sx, y + 22, sx + 44, y + 46, "switch_on" if theme_is_dark else "switch_off")
        self.circle(sx + (32 if theme_is_dark else 12), y + 34, "knob")
        self.hit(sx - 6, y + 16, sx + 50, y + 52, self.toggle_theme)
        y += 68 + 12

        # sound
        on = self.settings["sound_on"]
        self.rounded(x0, y, x1, y + 68, "card")
        self.text(x0 + 36, y + 34, ICON_VOLUME if on else ICON_MUTE, self.f_icon_big,
                  ACCENT if on else FAINT, "center")
        self.text(x0 + 72, y + 24, "Sound", self.f_row, TEXT)
        self.text(x0 + 72, y + 47, f"Plays when {avatar_info(self.settings['avatar'])['name']} comes over"
                  if on else "Muted", self.f_small, MUTED)
        self.rounded(sx, y + 22, sx + 44, y + 46, "switch_on" if on else "switch_off")
        self.circle(sx + (32 if on else 12), y + 34, "knob")
        self.hit(sx - 6, y + 16, sx + 50, y + 52, self.toggle_sound)
        y += 68 + 12

        # start with Windows (the registry is the record, so it can't disagree with Windows)
        on = starts_with_windows()
        self.rounded(x0, y, x1, y + 68, "card")
        self.text(x0 + 36, y + 34, ICON_POWER, self.f_icon_big, ACCENT if on else FAINT, "center")
        self.text(x0 + 72, y + 24, "Start with Windows", self.f_row, TEXT)
        self.text(x0 + 72, y + 47, "Waits in the tray from the moment you sign in" if on
                  else "Off: open Hey Buddy yourself to get reminders", self.f_small, MUTED)
        self.rounded(sx, y + 22, sx + 44, y + 46, "switch_on" if on else "switch_off")
        self.circle(sx + (32 if on else 12), y + 34, "knob")
        self.hit(sx - 6, y + 16, sx + 50, y + 52, self.toggle_startup)
        y += 68 + 12

        # avatar choice
        self.rounded(x0, y, x1, y + 68, "card")
        self.text(x0 + 36, y + 34, ICON_PERSON, self.f_icon_big, ACCENT, "center")
        self.text(x0 + 72, y + 24, "Avatar", self.f_row, TEXT)
        self.text(x0 + 72, y + 47, "Who comes over to remind you", self.f_small, MUTED)
        cx = x1 - 24
        options = [(PREVIEW_ID, "Preview")] + [(k, a["name"]) for k, a in reversed(AVATARS.items())]
        for key, label in options:                      # laid out right to left
            on = key == self.settings["avatar"]
            font = self.f_bold if on or key == PREVIEW_ID else self.f_text
            w = font.measure(label) + 34
            self.rounded(cx - w, y + 15, cx, y + 53, "chip_on" if on else "chip")
            self.text(cx - w // 2, y + 34, label, font, ACCENT if on or key == PREVIEW_ID else TEXT,
                      "center")
            action = self.preview_avatar if key == PREVIEW_ID else (lambda key=key: self.choose_avatar(key))
            self.hit(cx - w, y + 15, cx, y + 53, action)
            cx -= w + (18 if key == PREVIEW_ID else 10)
        return y + 68

    # --- actions ---

    def show_page(self, page):
        self.page = page
        self.canvas.focus_set()     # leave the text field, so typing doesn't go to a hidden box
        self.canvas.yview_moveto(0)
        self.render()

    def add_task(self):
        text = self.task_text()
        when = self.current_when()
        self.errors = {}
        if not text:
            self.errors["task"] = "Type what you want to be reminded about."
        if when <= dt.datetime.now():
            self.errors["when"] = "That time has already passed. Pick a later one."
        if self.errors:
            if "task" in self.errors:
                self.task_entry.focus_set()
            self.render()
            return
        self.tasks.append({
            "id": uuid.uuid4().hex,
            "text": text,
            "due": when.isoformat(timespec="minutes"),
            "done": False,
        })
        save_tasks(self.tasks)
        self.task_entry.delete(0, "end")
        self.when_mode = DEFAULT_WHEN
        self.render()

    def set_done(self, task):
        task["done"] = True
        task["done_at"] = dt.datetime.now().isoformat(timespec="minutes")

    def mark_done(self, task):
        self.set_done(task)
        save_tasks(self.tasks)
        self.render()

    def remove_task(self, task):
        self.tasks = [t for t in self.tasks if t["id"] != task["id"]]
        save_tasks(self.tasks)
        self.render()

    def delete_task(self, task):
        if messagebox.askyesno("Delete", f"Delete '{task['text']}'?"):
            self.remove_task(task)

    # --- water reminder ---

    def schedule_water(self, minutes=None):
        """Sets the next water reminder `minutes` from now (default: the chosen interval)."""
        if self.settings["water_on"]:
            wait = self.settings["water_minutes"] if minutes is None else minutes
            self.water_next = dt.datetime.now() + dt.timedelta(minutes=wait)
        else:
            self.water_next = None

    def toggle_water(self):
        self.settings["water_on"] = not self.settings["water_on"]
        save_settings(self.settings)
        self.schedule_water()
        self.render()

    def toggle_theme(self):
        self.settings["theme"] = "dark" if self.settings.get("theme") != "dark" else "light"
        apply_theme(self.settings["theme"])
        save_settings(self.settings)
        self.images = self.load_images()
        self.root.configure(bg=BG)
        self.canvas.configure(bg=BG)
        self.task_entry.config(bg=CARD, insertbackground=TEXT,
                               fg=FAINT if self.placeholder_on else TEXT)
        self.render()

    def toggle_sound(self):
        self.settings["sound_on"] = not self.settings["sound_on"]
        save_settings(self.settings)
        if self.popup is not None:          # takes effect on the reminder already on screen too
            self.popup.sound = self.settings["sound_on"]
        if not self.settings["sound_on"]:
            stop_sound()
        self.render()

    def toggle_startup(self):
        try:
            set_start_with_windows(not starts_with_windows())
        except (ImportError, OSError) as e:
            messagebox.showerror(APP_NAME, f"Couldn't change the Windows startup setting.\n\n{e}")
        self.render()

    def step_water(self, step):
        minutes = min(WATER_MAX, max(WATER_MIN, self.settings["water_minutes"] + step))
        if minutes != self.settings["water_minutes"]:
            self.settings["water_minutes"] = minutes
            save_settings(self.settings)
            self.schedule_water()
            self.render()

    # --- reminder loop ---

    def tick(self):
        now = dt.datetime.now()
        busy = set(self.queued)
        if self.current_id:
            busy.add(self.current_id)
        if self.water_next and self.water_next <= now and WATER_ID not in busy:
            self.queued.append(WATER_ID)
        for task in self.tasks:
            if task["done"] or task["id"] in busy:
                continue
            if dt.datetime.fromisoformat(task["due"]) <= now:
                self.queued.append(task["id"])
        self.show_next()
        if now.minute != self.shown_minute:     # keeps "in 3 min" and the default time current
            self.render()
        self.root.after(1000, self.tick)

    def show_next(self):
        if self.popup is not None or not self.queued:
            return
        tid = self.queued.pop(0)
        avatar, sound = self.settings["avatar"], self.settings["sound_on"]
        if tid == PREVIEW_ID:
            self.current_id = tid
            self.popup = ReminderPopup(
                self.root, "This is how I'll remind you.", lambda done: self.answer(PREVIEW_ID, done),
                heading=f"Hey buddy! I'm {avatar_info(avatar)['name']}.", avatar=avatar, sound=sound)
            return
        if tid == WATER_ID:
            if not self.settings["water_on"]:       # switched off while waiting its turn
                self.show_next()
                return
            self.current_id = tid
            self.popup = ReminderPopup(
                self.root, "Time for a glass of water.",
                lambda done: self.answer(WATER_ID, done), heading="Hey buddy! Had some water?",
                avatar=avatar, sound=sound)
            return
        task = next((t for t in self.tasks if t["id"] == tid), None)
        if task is None or task["done"]:
            self.show_next()
            return
        self.current_id = tid
        self.render()             # the task now shows as due
        self.popup = ReminderPopup(self.root, task["text"],
                                   lambda done, tid=tid: self.answer(tid, done),
                                   avatar=avatar, sound=sound)

    def answer(self, tid, done):
        self.popup = None
        self.current_id = None
        if tid == PREVIEW_ID:
            self.show_next()
            return
        if tid == WATER_ID:
            self.schedule_water(None if done else SNOOZE_MINUTES)
            self.render()
            self.show_next()
            return
        task = next((t for t in self.tasks if t["id"] == tid), None)
        if task is not None:
            if done:
                self.set_done(task)
            else:
                later = dt.datetime.now() + dt.timedelta(minutes=SNOOZE_MINUTES)
                task["due"] = later.isoformat(timespec="minutes")
            save_tasks(self.tasks)
            self.render()
        self.show_next()


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    if claim_single_instance():
        try:
            import ctypes       # taskbar shows Hey Buddy's own icon, not Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HeyBuddy.App")
        except (AttributeError, OSError):
            pass
        root = tk.Tk()
        ConsoleApp(root, background=BACKGROUND_ARG in sys.argv)
        root.mainloop()
