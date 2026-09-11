"""Focus Miner 4.1 - a low-distraction idle and focus game for Windows.

After 20 seconds without keyboard or mouse input, the miner automatically
explores and improves the camp. The game pauses as soon as input resumes.
Python 3.9+ and no third-party packages are required.
"""

import copy
import ctypes
import json
import math
import os
import random
import shutil
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

APP_NAME = "Focus Miner"
VERSION = "4.1"

# Three fixed layouts keep the game readable without relying on OS scaling.
# The canvas is always 28 px narrower than the outer window, matching the
# original 13 px side padding plus the canvas highlight border.
WINDOW_PRESETS = {
    "large":  {"window": (520, 468), "canvas": (492, 282)},
    "medium": {"window": (420, 418), "canvas": (392, 238)},
    "small":  {"window": (330, 376), "canvas": (302, 202)},
}
WINDOW_W, WINDOW_H = WINDOW_PRESETS["large"]["window"]
CANVAS_W, CANVAS_H = WINDOW_PRESETS["large"]["canvas"]
WORLD_MIN_X, WORLD_MAX_X = -2, 32
WORLD_MIN_Y, WORLD_MAX_Y = -2, 19
IDLE_SECONDS = 20
SAVE_INTERVAL = 10
FRAME_MS = 16  # ~60 FPS target; Tk/Windows may vary slightly.
GAME_SPEEDS = (1, 2, 5, 20)
BG, PANEL = "#071525", "#0d2942"
TEXT, MUTED, ACCENT = "#f4faff", "#b7cce2", "#42c7ff"

# Focus time steadily opens new expedition themes. Regions still control the
# resources gathered; biomes change the atmosphere and long-term destination.
BIOMES = (
    {"key": "crystal", "name": "Crystal Caverns", "unlock": 0,
     "bands": ("#06101d", "#081827", "#092238", "#0b2c46", "#103752", "#154361", "#1b4f70"),
     "glow": "#42c7ff"},
    {"key": "fungal", "name": "Fungal Grotto", "unlock": 5400,
     "bands": ("#08131a", "#0d1e25", "#112d31", "#173b38", "#1f4940", "#285747", "#35664f"),
     "glow": "#6af0a7"},
    {"key": "ruins", "name": "Buried Ruins", "unlock": 14400,
     "bands": ("#130f1e", "#1b162a", "#241d37", "#30254a", "#3c2d59", "#49376a", "#58447a"),
     "glow": "#c08cff"},
    {"key": "frost", "name": "Frozen Mine", "unlock": 28800,
     "bands": ("#071421", "#0b2030", "#102d40", "#173b50", "#214b61", "#2d5c73", "#3b6e86"),
     "glow": "#a9eeff"},
    {"key": "magma", "name": "Magma Depths", "unlock": 54000,
     "bands": ("#170b10", "#241018", "#35151c", "#4a1d20", "#612724", "#793326", "#93442b"),
     "glow": "#ff8a47"},
    {"key": "city", "name": "Lost Underground City", "unlock": 90000,
     "bands": ("#0d101b", "#14192a", "#1c2439", "#263149", "#33405a", "#41516a", "#51647c"),
     "glow": "#ffd76b"},
)

# Buildings construct automatically. During the final 20% before an unlock,
# a foundation rises in stages; reaching the threshold completes the building.
SETTLEMENT_BUILDINGS = (
    {"key": "store", "name": "Storage Shed", "unlock": 600, "pos": (12.2, 14.0), "color": "#d58a46"},
    {"key": "museum", "name": "Museum Tent", "unlock": 1200, "pos": (18.0, 13.5), "color": "#a779dc"},
    {"key": "workshop", "name": "Workshop", "unlock": 2400, "pos": (11.0, 17.2), "color": "#5aa9d6"},
    {"key": "station", "name": "Rail Station", "unlock": 4200, "pos": (19.0, 17.0), "color": "#e0b45a"},
    {"key": "lab", "name": "Crystal Lab", "unlock": 7200, "pos": (8.5, 12.5), "color": "#5adbd3"},
    {"key": "cabins", "name": "Crew Cabins", "unlock": 12600, "pos": (21.5, 14.0), "color": "#d06c5c"},
    {"key": "drill", "name": "Deep Drill", "unlock": 21600, "pos": (7.0, 17.5), "color": "#d9e3ed"},
    {"key": "tower", "name": "Survey Tower", "unlock": 36000, "pos": (23.0, 18.0), "color": "#7db9ea"},
    {"key": "hall", "name": "Engineers' Hall", "unlock": 57600, "pos": (15.0, 20.0), "color": "#efb25e"},
    {"key": "monument", "name": "Cavern Monument", "unlock": 90000, "pos": (15.0, 11.0), "color": "#c698ff"},
)

EVENTS = (
    ("Rich mineral vein", "cargo"),
    ("Lost supply crate recovered", "coins"),
    ("Helper found a shortcut", "time"),
    ("Glowing crystal chamber discovered", "discovery"),
    ("Old equipment salvaged", "stone"),
)


def blend(color_a, color_b, amount):
    """Blend two #RRGGBB colours. Used for inexpensive light and depth."""
    amount = max(0.0, min(1.0, amount))
    first = tuple(int(color_a[index:index + 2], 16) for index in (1, 3, 5))
    second = tuple(int(color_b[index:index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(round(a + (b - a) * amount) for a, b in zip(first, second))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]


def idle_seconds():
    """Return inactivity in the current Windows session."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError("Windows could not report idle time")
    current = ctypes.windll.kernel32.GetTickCount64() & 0xFFFFFFFF
    return ((current - info.dwTime) & 0xFFFFFFFF) / 1000.0


def current_monitor_area():
    """Usable bounds of the monitor holding the pointer, or None."""
    try:
        user32 = ctypes.windll.user32
        point = POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        user32.MonitorFromPoint.argtypes = (POINT, ctypes.c_uint)
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor = user32.MonitorFromPoint(point, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom
    except (AttributeError, OSError):
        return None


def fmt_time(value):
    value = max(0, int(value))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


REGIONS = (
    {"key": "stone", "name": "Deep Stone", "pos": (0.0, 10.0), "unlock": 0, "tint": "#d4ecff"},
    {"key": "coins", "name": "Amber Hollow", "pos": (7.0, 2.0), "unlock": 300, "tint": "#ffd75e"},
    {"key": "diamonds", "name": "Crystal Ridge", "pos": (23.0, 2.0), "unlock": 900, "tint": "#54e4ff"},
    {"key": "gems", "name": "Emerald Shelf", "pos": (30.0, 10.0), "unlock": 1800, "tint": "#54f29a"},
    {"key": "mixed", "name": "Ancient Vault", "pos": (15.0, 0.0), "unlock": 3600, "tint": "#c98bff"},
)

MILESTONES = (
    (300, "Lantern trail and Amber Hollow"),
    (900, "Helper and Crystal Ridge"),
    (1800, "Minecart and Emerald Shelf"),
    (3600, "Workshop and Ancient Vault"),
    (10800, "Permanent mining outpost"),
    (36000, "Underground settlement"),
)

RANKS = (
    (0, "Prospector"),
    (1800, "Miner"),
    (7200, "Foreman"),
    (36000, "Chief Engineer"),
    (90000, "Cavern Architect"),
)

DISCOVERIES = {
    "stone": ("Quartz Cluster", "Spiral Fossil"),
    "coins": ("Sun Amber", "Old Mining Token"),
    "diamonds": ("Blue Geode", "Star Sapphire"),
    "gems": ("Glow Moss", "Royal Emerald"),
    "mixed": ("Ancient Compass", "Meteor Fragment"),
}

DEFAULTS = {
    "version": 4,
    "run_mode": "idle",
    "window_size": "large",
    "stone": 0,
    "diamonds": 0,
    "gems": 0,
    "coins": 0,
    "pickaxe_level": 1,
    "luck_level": 1,
    "boots_level": 1,
    "bag_level": 1,
    "total_focus_seconds": 0.0,
    "trips": 0,
    "sessions": 0,
    "contracts_completed": 0,
    "contract_progress": 0,
    "longest_session": 0.0,
    "discoveries": [],
    "discovery_pity": 0,
    "diamond_pity": 0,
    "gem_pity": 0,
    "active_biome": "crystal",
    "focus_task": "",
    "focus_duration": 1500,
    "focus_end_time": 0.0,
    "break_enabled": True,
    "break_end_time": 0.0,
    "daily_goal": 3600,
    "session_history": [],
    "specialisation": "",
    "helmet_color": "#ffdb4d",
    "sound_enabled": True,
    "reduced_motion": False,
    "always_on_top": True,
    "target_fps": 60,
    "game_speed": 1,
    "events_seen": 0,
}


class SaveStore:
    """Validated, atomic saves with a backup and old-save migration."""

    def __init__(self):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".focus_miner"))
        self.folder = base / "FocusMiner" if os.environ.get("LOCALAPPDATA") else base
        self.path = self.folder / "save.json"
        self.backup = self.folder / "save.backup.json"
        self.legacy = Path.home() / "focus_miner_save.json"
        self.error = ""

    def load(self):
        for path in (self.path, self.backup, self.legacy):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                state = copy.deepcopy(DEFAULTS)
                if not isinstance(raw, dict):
                    continue
                for key in state:
                    if key in raw:
                        state[key] = raw[key]
                for key in ("stone", "diamonds", "gems", "coins", "trips", "sessions",
                            "contracts_completed", "contract_progress", "events_seen"):
                    state[key] = max(0, int(state[key]))
                for key in ("pickaxe_level", "luck_level", "boots_level", "bag_level"):
                    state[key] = max(1, int(state[key]))
                state["total_focus_seconds"] = max(0.0, float(state["total_focus_seconds"]))
                state["longest_session"] = max(0.0, float(state["longest_session"]))
                if state["run_mode"] not in ("idle", "task", "focus", "break"):
                    state["run_mode"] = "idle"
                if state.get("window_size") not in WINDOW_PRESETS:
                    state["window_size"] = "large"
                valid_names = {name for group in DISCOVERIES.values() for name in group}
                state["discoveries"] = list(dict.fromkeys(
                    name for name in state["discoveries"] if name in valid_names
                )) if isinstance(state["discoveries"], list) else []
                valid_biomes = {item["key"] for item in BIOMES
                                if state["total_focus_seconds"] >= item["unlock"]}
                if state["active_biome"] not in valid_biomes:
                    state["active_biome"] = "crystal"
                state["focus_task"] = str(state["focus_task"])[:80]
                state["focus_duration"] = max(300, min(14400, int(state["focus_duration"])))
                state["focus_end_time"] = max(0.0, float(state["focus_end_time"]))
                state["break_end_time"] = max(0.0, float(state["break_end_time"]))
                if state["run_mode"] == "focus" and state["focus_end_time"] <= time.time():
                    state["run_mode"], state["focus_end_time"] = "idle", 0.0
                if state["run_mode"] == "break" and state["break_end_time"] <= time.time():
                    state["run_mode"], state["break_end_time"] = "idle", 0.0
                if not isinstance(state["session_history"], list):
                    state["session_history"] = []
                state["session_history"] = [
                    item for item in state["session_history"][-90:]
                    if isinstance(item, dict) and isinstance(item.get("seconds"), (int, float))
                ]
                if state["specialisation"] not in ("", "explorer", "engineer", "collector", "merchant"):
                    state["specialisation"] = ""
                if state["helmet_color"] not in ("#ffdb4d", "#69ddff", "#6be6a0", "#c98bff", "#ff8a65"):
                    state["helmet_color"] = "#ffdb4d"
                for key in ("sound_enabled", "reduced_motion", "always_on_top", "break_enabled"):
                    state[key] = bool(state[key])
                state["target_fps"] = 30 if int(state["target_fps"]) <= 30 else 60
                state["game_speed"] = int(state["game_speed"])
                if state["game_speed"] not in GAME_SPEEDS:
                    state["game_speed"] = 1
                state["daily_goal"] = max(900, min(28800, int(state["daily_goal"])))
                state["version"] = 4
                return state
            except (OSError, ValueError, TypeError):
                pass
        return copy.deepcopy(DEFAULTS)

    def save(self, state):
        temporary = self.folder / "save.tmp"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                try:
                    json.loads(self.path.read_text(encoding="utf-8"))
                    shutil.copy2(self.path, self.backup)
                except (OSError, ValueError):
                    pass
            os.replace(temporary, self.path)
            self.error = ""
            return True
        except OSError as exc:
            self.error = f"Save failed: {exc}"
            return False


class FocusMiner:
    BASE = (15.0, 16.0)

    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} {VERSION}")
        root.resizable(False, False)
        root.configure(bg=BG)

        self.store = SaveStore()
        self.state = self.store.load()
        root.attributes("-topmost", self.state["always_on_top"])
        self.run_mode = self.state["run_mode"]
        self.window_size = self.state.get("window_size", "large")
        self.game_speed = self.state["game_speed"]
        self.active = False
        self.session_time = 0.0
        self.session_trips = 0
        self.session_gains = {key: 0 for key in ("stone", "diamonds", "gems", "coins")}
        self.session_upgrades = []
        self.session_unlocks = []
        self.session_finds = []
        self.session_buildings = []
        self.session_contracts = 0
        self.last_update = self.last_save = time.monotonic()
        self.last_render = self.last_labels = 0.0
        self.window_motion_until = 0.0
        self.animation = 0.0
        self.phase = "waiting"
        self.phase_time = 0.0
        self.position = list(self.BASE)
        self.camera_position = list(self.BASE)
        self.target = None
        self.cargo = {key: 0 for key in self.session_gains}
        self.event = "Waiting for 20 seconds without keyboard or mouse input."
        self.banner_until = 0.0
        self.report_until = 0.0
        self.report = []
        self.particles = []
        self.floaters = []
        self.impact_rings = []
        self.camera_kick = 0.0
        self.last_mining_burst = -1
        self.last_settlement_count = self.settlement_count()
        self.active_event = None
        self.active_event_until = 0.0
        self.popups = []
        self.ambient_dust = [
            {
                "x": random.Random(710 + index).uniform(12, CANVAS_W - 12),
                "y": random.Random(910 + index).uniform(18, CANVAS_H - 30),
                "phase": random.Random(1110 + index).uniform(0, math.tau),
                "size": random.Random(1310 + index).choice((1, 1, 1, 2)),
            }
            for index in range(24)
        ]

        self.build_ui()
        self.apply_size_preset(self.window_size, reposition=False)
        self.position_window()
        root.bind("<Configure>", self.on_window_configure, add="+")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.loop()

    def build_ui(self):
        self.header = tk.Frame(self.root, bg=BG)
        self.header.pack(fill="x", padx=13, pady=(7, 4))

        self.title_label = tk.Label(self.header, text="FOCUS MINER", fg=TEXT, bg=BG,
                                    font=("Segoe UI", 11, "bold"))
        self.title_label.pack(side="left")
        self.version_label = tk.Label(self.header, text=f"  v{VERSION}", fg="#7da9cc", bg=BG,
                                      font=("Segoe UI", 7))
        self.version_label.pack(side="left", pady=(3, 0))
        self.rank_label = tk.Label(self.header, text="", fg="#e1b2ff", bg=BG,
                                   font=("Segoe UI", 7, "bold"))
        self.rank_label.pack(side="left", padx=(8, 0), pady=(3, 0))

        self.menu_button = tk.Button(
            self.header, text="MENU", width=5, relief="flat", borderwidth=0,
            cursor="hand2", fg=TEXT, bg="#69439b", activeforeground=TEXT,
            activebackground="#8b5fc1", font=("Segoe UI", 7, "bold"),
            command=self.open_control_center,
        )
        self.menu_button.pack(side="right", padx=(4, 0))

        # Compact S/M/L controls stay available in every layout.
        self.size_buttons = {}
        for key, label in (("small", "S"), ("medium", "M"), ("large", "L")):
            button = tk.Button(
                self.header, text=label, width=2, relief="flat", borderwidth=0,
                cursor="hand2", fg=TEXT, bg="#173650", activeforeground=TEXT,
                activebackground="#285b82", font=("Segoe UI", 7, "bold"),
                command=lambda selected=key: self.apply_size_preset(selected),
            )
            button.pack(side="right", padx=(2, 0))
            self.size_buttons[key] = button

        self.mode_toggle = tk.Button(
            self.header, command=self.toggle_run_mode, text="", width=9,
            relief="flat", borderwidth=0, cursor="hand2", fg=TEXT,
            font=("Segoe UI", 7, "bold"), activeforeground=TEXT,
        )
        self.mode_toggle.pack(side="right", padx=(0, 4))
        self.update_mode_button()

        self.speed_button = tk.Button(
            self.header, command=self.cycle_game_speed, text="", width=3,
            relief="flat", borderwidth=0, cursor="hand2", fg=TEXT,
            bg="#9a5d25", activeforeground=TEXT, activebackground="#c07831",
            font=("Segoe UI", 7, "bold"),
        )
        self.speed_button.pack(side="right", padx=(0, 4))
        self.update_speed_button()

        # Status gets its own thin row, preventing header controls from colliding
        # in Medium and Small modes.
        self.status_row = tk.Frame(self.root, bg=BG)
        self.status_row.pack(fill="x", padx=13, pady=(0, 3))
        self.mode = tk.Label(self.status_row, anchor="w", fg=MUTED, bg=BG,
                             font=("Segoe UI", 8, "bold"))
        self.mode.pack(side="left", fill="x", expand=True)

        self.canvas = tk.Canvas(self.root, width=CANVAS_W, height=CANVAS_H, bg=PANEL,
                                highlightthickness=1, highlightbackground="#43caff")
        self.canvas.pack(padx=13)
        self.progress = tk.Canvas(self.root, width=CANVAS_W, height=25, bg=BG, highlightthickness=0)
        self.progress.pack(padx=13, pady=(2, 0))

        self.info_row = tk.Frame(self.root, bg=BG)
        self.info_row.pack(fill="x", padx=13)
        self.resources = tk.Label(self.info_row, justify="left", anchor="w", fg=TEXT, bg=BG,
                                  font=("Consolas", 9))
        self.resources.pack(side="left", fill="x", expand=True)
        self.upgrades = tk.Label(self.info_row, justify="right", anchor="e", fg="#d8e8f8", bg=BG,
                                 font=("Segoe UI", 8))
        self.upgrades.pack(side="right")

        self.footer = tk.Frame(self.root, bg=BG)
        self.footer.pack(fill="x", padx=13, pady=(2, 6))
        self.event_label = tk.Label(self.footer, anchor="w", fg=ACCENT, bg=BG,
                                    font=("Segoe UI", 8))
        self.event_label.pack(side="left", fill="x", expand=True)
        self.museum = tk.Label(self.footer, anchor="e", fg="#ffd75e", bg=BG,
                               font=("Segoe UI", 8, "bold"))
        self.museum.pack(side="right")

    def apply_size_preset(self, name, reposition=True):
        """Switch between Large, Medium and Small without scaling the OS window."""
        global WINDOW_W, WINDOW_H, CANVAS_W, CANVAS_H
        if name not in WINDOW_PRESETS:
            name = "large"

        old_x, old_y = self.root.winfo_x(), self.root.winfo_y()
        self.window_size = name
        self.state["window_size"] = name
        WINDOW_W, WINDOW_H = WINDOW_PRESETS[name]["window"]
        CANVAS_W, CANVAS_H = WINDOW_PRESETS[name]["canvas"]

        self.canvas.config(width=CANVAS_W, height=CANVAS_H)
        self.progress.config(width=CANVAS_W)

        # Rebuild ambient dust against the new viewport so Small mode is still full.
        self.ambient_dust = [
            {
                "x": random.Random(710 + index).uniform(12, CANVAS_W - 12),
                "y": random.Random(910 + index).uniform(18, max(19, CANVAS_H - 30)),
                "phase": random.Random(1110 + index).uniform(0, math.tau),
                "size": random.Random(1310 + index).choice((1, 1, 1, 2)),
            }
            for index in range(18 if name == "small" else 24)
        ]

        # Tighten secondary information before reducing font size.
        if name == "large":
            self.version_label.pack(side="left", pady=(3, 0))
            self.rank_label.pack(side="left", padx=(8, 0), pady=(3, 0))
            self.title_label.config(font=("Segoe UI", 11, "bold"), text="FOCUS MINER")
            self.menu_button.config(width=5, font=("Segoe UI", 7, "bold"))
            self.mode_toggle.config(width=9, font=("Segoe UI", 7, "bold"))
            self.speed_button.config(width=3, font=("Segoe UI", 7, "bold"))
            for button in self.size_buttons.values():
                button.config(width=2, font=("Segoe UI", 7, "bold"))
            self.resources.config(font=("Consolas", 9))
            self.upgrades.config(font=("Segoe UI", 8))
            self.event_label.config(font=("Segoe UI", 8), wraplength=310)
            self.museum.config(font=("Segoe UI", 8, "bold"))
        elif name == "medium":
            self.version_label.pack_forget()
            self.rank_label.pack_forget()
            self.title_label.config(font=("Segoe UI", 9, "bold"), text="FOCUS MINER")
            self.menu_button.config(width=5, font=("Segoe UI", 7, "bold"))
            self.mode_toggle.config(width=8, font=("Segoe UI", 7, "bold"))
            self.speed_button.config(width=3, font=("Segoe UI", 7, "bold"))
            for button in self.size_buttons.values():
                button.config(width=2, font=("Segoe UI", 7, "bold"))
            self.resources.config(font=("Consolas", 8))
            self.upgrades.config(font=("Segoe UI", 7))
            self.event_label.config(font=("Segoe UI", 7), wraplength=225)
            self.museum.config(font=("Segoe UI", 7, "bold"))
        else:
            self.version_label.pack_forget()
            self.rank_label.pack_forget()
            self.title_label.config(font=("Segoe UI", 7, "bold"), text="FOCUS MINER")
            self.menu_button.config(width=4, font=("Segoe UI", 6, "bold"))
            self.mode_toggle.config(width=6, font=("Segoe UI", 6, "bold"))
            self.speed_button.config(width=3, font=("Segoe UI", 6, "bold"))
            for button in self.size_buttons.values():
                button.config(width=1, font=("Segoe UI", 6, "bold"))
            self.resources.config(font=("Consolas", 7))
            self.upgrades.config(font=("Segoe UI", 7))
            self.event_label.config(font=("Segoe UI", 7), wraplength=175)
            self.museum.config(font=("Segoe UI", 7, "bold"))

        for key, button in self.size_buttons.items():
            button.config(bg="#2688c4" if key == name else "#173650")
        self.update_mode_button()
        self.update_speed_button()

        self.root.update_idletasks()
        if reposition:
            # Preserve the window's top-left location when switching size.
            self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+{old_x}+{old_y}")
            self.save()
        else:
            self.root.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.draw_world()

    def on_window_configure(self, event):
        """Mark native window moves/resizes so expensive world redraws can pause briefly."""
        if event.widget is self.root:
            self.window_motion_until = time.monotonic() + 0.075

    def position_window(self):
        self.root.update_idletasks()
        area = current_monitor_area()
        if area:
            left, top, right, bottom = area
            x = max(left, right - WINDOW_W - 14)
            y = max(top, bottom - WINDOW_H - 14)
        else:
            x = max(0, self.root.winfo_screenwidth() - WINDOW_W - 14)
            y = max(0, self.root.winfo_screenheight() - WINDOW_H - 58)
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+{y}")

    def new_popup(self, title, width=430, height=430):
        """Create one consistent, disposable tool window."""
        for popup in self.popups[:]:
            try:
                popup.destroy()
            except tk.TclError:
                pass
        self.popups.clear()
        popup = tk.Toplevel(self.root)
        popup.title(f"{APP_NAME} — {title}")
        popup.configure(bg=BG)
        popup.resizable(False, False)
        popup.attributes("-topmost", self.state["always_on_top"])
        x = max(0, self.root.winfo_x() - width - 10)
        y = max(0, self.root.winfo_y())
        popup.geometry(f"{width}x{height}+{x}+{y}")
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)
        self.popups.append(popup)
        return popup

    @staticmethod
    def popup_title(parent, title, subtitle=""):
        tk.Label(parent, text=title, fg=TEXT, bg=BG,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        if subtitle:
            tk.Label(parent, text=subtitle, fg=MUTED, bg=BG, justify="left",
                     wraplength=390, font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 12))

    def menu_action(self, parent, label, command, color="#173f5f"):
        button = tk.Button(parent, text=label, command=command, relief="flat",
                           borderwidth=0, cursor="hand2", fg=TEXT, bg=color,
                           activeforeground=TEXT, activebackground=blend(color, "#ffffff", .16),
                           font=("Segoe UI", 10, "bold"), pady=8)
        button.pack(fill="x", padx=18, pady=4)
        return button

    def open_control_center(self):
        popup = self.new_popup("Expedition Centre", 410, 510)
        self.popup_title(popup, "EXPEDITION CENTRE",
                         "Plan a focus run or inspect everything your expeditions have built.")
        self.menu_action(popup, "START FOCUS SESSION", self.open_focus_dialog, "#237853")
        self.menu_action(popup, "SETTLEMENT", self.open_settlement)
        self.menu_action(popup, "MUSEUM", self.open_museum, "#73509b")
        self.menu_action(popup, "EXPEDITION MAP", self.open_map, "#315f8d")
        self.menu_action(popup, "HISTORY & STATISTICS", self.open_statistics, "#295b6d")
        self.menu_action(popup, "CAREER & COSMETICS", self.open_career, "#825c2c")
        self.menu_action(popup, "ACHIEVEMENTS", self.open_achievements, "#75652d")
        self.menu_action(popup, "SETTINGS & SAVE", self.open_settings, "#3e4b5b")

    def open_focus_dialog(self):
        popup = self.new_popup("Focus Session", 420, 390)
        self.popup_title(popup, "START A FOCUS SESSION",
                         "Mining continues while you use the laptop. Partial progress is always kept.")
        tk.Label(popup, text="TASK NAME", fg=MUTED, bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=18)
        task = tk.Entry(popup, bg="#102b42", fg=TEXT, insertbackground=TEXT,
                        relief="flat", font=("Segoe UI", 10))
        task.insert(0, self.state["focus_task"])
        task.pack(fill="x", padx=18, pady=(4, 14), ipady=7)
        tk.Label(popup, text="DURATION", fg=MUTED, bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=18)
        duration = tk.IntVar(value=max(15, self.state["focus_duration"] // 60))
        break_after = tk.BooleanVar(value=self.state["break_enabled"])
        row = tk.Frame(popup, bg=BG)
        row.pack(fill="x", padx=14, pady=8)
        for minutes in (15, 25, 45, 60):
            tk.Radiobutton(row, text=f"{minutes}m", variable=duration, value=minutes,
                           indicatoron=False, selectcolor="#25895f", fg=TEXT, bg="#173650",
                           activebackground="#285b82", activeforeground=TEXT,
                           relief="flat", width=7, pady=7,
                           font=("Segoe UI", 9, "bold")).pack(side="left", padx=4)
        tk.Label(popup, text="A completion bonus awards one extra contract step.\nA short session still saves all time and resources.",
                 fg="#8fb4cf", bg=BG, justify="left", font=("Segoe UI", 8)).pack(anchor="w", padx=18, pady=8)
        tk.Checkbutton(popup, text="Begin a five-minute break when complete",
                       variable=break_after, selectcolor="#173650", fg=TEXT, bg=BG,
                       activebackground=BG, activeforeground=TEXT,
                       font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 5))

        def start():
            self.state["break_enabled"] = break_after.get()
            self.start_focus_session(task.get().strip(), duration.get() * 60)
            popup.destroy()

        self.menu_action(popup, "BEGIN EXPEDITION", start, "#237853")
        task.focus_set()

    def start_focus_session(self, task, duration):
        now = time.monotonic()
        if self.active:
            self.end_session(now, completed=False)
        self.state["focus_task"] = task[:80] or "Focused work"
        self.state["focus_duration"] = int(duration)
        self.state["focus_end_time"] = time.time() + duration
        self.run_mode = self.state["run_mode"] = "focus"
        self.update_mode_button()
        self.begin_session(now)
        self.event = f"Focus session: {self.state['focus_task']}."
        self.save()

    def open_settlement(self):
        popup = self.new_popup("Settlement", 440, 500)
        built = self.settlement_count()
        self.popup_title(popup, f"SETTLEMENT  {built}/{len(SETTLEMENT_BUILDINGS)}",
                         "Buildings construct automatically as your lifetime focus time grows.")
        body = tk.Frame(popup, bg=BG)
        body.pack(fill="both", expand=True, padx=18)
        total = self.state["total_focus_seconds"]
        for building in SETTLEMENT_BUILDINGS:
            progress = self.building_progress(building)
            if progress >= 1:
                status, color = "BUILT", "#64e3a5"
            elif progress > 0:
                status, color = f"BUILDING {round(progress * 100)}%", "#ffd36a"
            else:
                status, color = f"IN {fmt_time(building['unlock'] - total)}", MUTED
            line = tk.Frame(body, bg="#0c253b")
            line.pack(fill="x", pady=2)
            tk.Label(line, text=building["name"], fg=TEXT, bg="#0c253b",
                     font=("Segoe UI", 9, "bold")).pack(side="left", padx=9, pady=7)
            tk.Label(line, text=status, fg=color, bg="#0c253b",
                     font=("Segoe UI", 8, "bold")).pack(side="right", padx=9)

    def open_museum(self):
        popup = self.new_popup("Museum", 440, 480)
        found = set(self.state["discoveries"])
        self.popup_title(popup, f"MUSEUM  {len(found)}/10",
                         "Every exhibit was recovered automatically during a focus expedition.")
        for region in REGIONS:
            frame = tk.Frame(popup, bg="#0c253b")
            frame.pack(fill="x", padx=18, pady=3)
            tk.Label(frame, text=region["name"], width=15, anchor="w", fg=region["tint"],
                     bg="#0c253b", font=("Segoe UI", 8, "bold")).pack(side="left", padx=8, pady=7)
            names = [name if name in found else "Unknown exhibit" for name in DISCOVERIES[region["key"]]]
            tk.Label(frame, text="  |  ".join(names), anchor="w", fg=TEXT, bg="#0c253b",
                     font=("Segoe UI", 8)).pack(side="left")

    def unlocked_biomes(self):
        total = self.state["total_focus_seconds"]
        return [biome for biome in BIOMES if total >= biome["unlock"]]

    def current_biome(self):
        return next((item for item in BIOMES if item["key"] == self.state["active_biome"]), BIOMES[0])

    def open_map(self):
        popup = self.new_popup("Expedition Map", 440, 460)
        self.popup_title(popup, "EXPEDITION MAP",
                         "Choose the atmosphere for future runs. New destinations unlock through focus time.")
        total = self.state["total_focus_seconds"]
        for biome in BIOMES:
            unlocked = total >= biome["unlock"]
            selected = biome["key"] == self.state["active_biome"]
            text = f"{biome['name']}  —  {'SELECTED' if selected else 'AVAILABLE'}" if unlocked else f"{biome['name']}  —  unlocks in {fmt_time(biome['unlock'] - total)}"
            button = tk.Button(popup, text=text, anchor="w", relief="flat", borderwidth=0,
                               fg=TEXT if unlocked else "#6f8396", bg="#256b70" if selected else "#102b42",
                               activeforeground=TEXT, activebackground="#285b82",
                               font=("Segoe UI", 9, "bold"), padx=10, pady=9,
                               state="normal" if unlocked else "disabled",
                               command=lambda key=biome["key"]: self.select_biome(key))
            button.pack(fill="x", padx=18, pady=3)

    def select_biome(self, key):
        if key in {item["key"] for item in self.unlocked_biomes()}:
            self.state["active_biome"] = key
            self.event = f"Next expeditions set for {self.current_biome()['name']}."
            self.save()
            self.open_map()

    def open_statistics(self):
        popup = self.new_popup("History", 450, 500)
        history = self.state["session_history"]
        today = time.strftime("%Y-%m-%d", time.localtime())
        today_seconds = sum(float(item.get("seconds", 0)) for item in history if item.get("date") == today)
        week = {}
        for item in history[-90:]:
            day = item.get("date", "Unknown")
            week[day] = week.get(day, 0) + float(item.get("seconds", 0))
        self.popup_title(popup, "FOCUS HISTORY",
                         f"Today {fmt_time(today_seconds)} / {fmt_time(self.state['daily_goal'])}  |  Total {fmt_time(self.state['total_focus_seconds'])}  |  Sessions {self.state['sessions']}")
        recent = list(week.items())[-7:]
        max_seconds = max([seconds for _, seconds in recent] or [1])
        chart = tk.Canvas(popup, width=410, height=145, bg="#0b2236", highlightthickness=0)
        chart.pack(padx=18, pady=(0, 10))
        for index, (day, seconds) in enumerate(recent):
            x = 18 + index * 56
            height = 92 * seconds / max_seconds
            chart.create_rectangle(x, 112 - height, x + 34, 112, fill="#35a8df", outline="")
            chart.create_text(x + 17, 124, text=day[-5:], fill=MUTED, font=("Segoe UI", 7))
            chart.create_text(x + 17, max(10, 106 - height), text=f"{round(seconds / 60)}m", fill=TEXT, font=("Segoe UI", 7, "bold"))
        tk.Label(popup, text="RECENT EXPEDITIONS", fg=MUTED, bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=18)
        for item in reversed(history[-6:]):
            task = str(item.get("task") or "Idle expedition")[:28]
            mark = "COMPLETE" if item.get("completed") else "SAVED"
            tk.Label(popup, text=f"{item.get('date', '')}   {fmt_time(item.get('seconds', 0))}   {task}   {mark}",
                     anchor="w", fg=TEXT, bg="#0c253b", font=("Segoe UI", 8), padx=8, pady=5).pack(fill="x", padx=18, pady=1)

    def open_career(self):
        popup = self.new_popup("Career", 440, 500)
        current = self.state["specialisation"]
        available = self.state["total_focus_seconds"] >= 1800
        self.popup_title(popup, "CAREER & COSMETICS",
                         "Choose one permanent specialty after 30 minutes of focus. It changes expedition bonuses, not difficulty.")
        careers = (
            ("explorer", "EXPLORER", "20% faster walking"),
            ("engineer", "ENGINEER", "15% faster mining"),
            ("collector", "COLLECTOR", "Better discovery chance"),
            ("merchant", "MERCHANT", "25% stronger contract rewards"),
        )
        for key, name, detail in careers:
            selected = current == key
            enabled = available and not current
            text = f"{name} — {detail}" + ("  [SELECTED]" if selected else "")
            tk.Button(popup, text=text, anchor="w", command=lambda value=key: self.select_specialisation(value),
                      state="normal" if enabled else "disabled", relief="flat", borderwidth=0,
                      fg=TEXT, disabledforeground="#7ea0b8" if not selected else "#70e7aa",
                      bg="#173650", activebackground="#285b82", activeforeground=TEXT,
                      font=("Segoe UI", 9, "bold"), padx=10, pady=8).pack(fill="x", padx=18, pady=2)
        tk.Label(popup, text="HELMET COLOUR", fg=MUTED, bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=18, pady=(16, 6))
        row = tk.Frame(popup, bg=BG)
        row.pack(anchor="w", padx=14)
        for index, color in enumerate(("#ffdb4d", "#69ddff", "#6be6a0", "#c98bff", "#ff8a65")):
            unlocked = index == 0 or self.state["total_focus_seconds"] >= index * 3600
            tk.Button(row, text="SELECT" if color == self.state["helmet_color"] else f"{index}h",
                      width=7, bg=color if unlocked else "#354354", fg="#08131d",
                      activebackground=blend(color, "#ffffff", .2), relief="flat",
                      state="normal" if unlocked else "disabled",
                      command=lambda value=color: self.select_helmet(value),
                      font=("Segoe UI", 7, "bold")).pack(side="left", padx=4)

    def achievements(self):
        s = self.state
        return (
            ("First Haul", "Complete one mining trip", s["trips"] >= 1),
            ("Deep Work", "Focus for 30 minutes in total", s["total_focus_seconds"] >= 1800),
            ("Shift Leader", "Focus for three hours in total", s["total_focus_seconds"] >= 10800),
            ("Cavern Regular", "Focus for ten hours in total", s["total_focus_seconds"] >= 36000),
            ("Town Planner", "Construct five settlement buildings", self.settlement_count() >= 5),
            ("City Founder", "Complete the entire settlement", self.settlement_count() >= 10),
            ("Curator", "Recover all ten museum exhibits", len(s["discoveries"]) >= 10),
            ("Reliable Supplier", "Complete 25 contracts", s["contracts_completed"] >= 25),
            ("Pathfinder", "Complete 100 mining trips", s["trips"] >= 100),
            ("World Explorer", "Unlock every expedition destination", len(self.unlocked_biomes()) == len(BIOMES)),
        )

    def open_achievements(self):
        popup = self.new_popup("Achievements", 440, 520)
        achievements = self.achievements()
        complete = sum(item[2] for item in achievements)
        self.popup_title(popup, f"ACHIEVEMENTS  {complete}/{len(achievements)}",
                         "Achievements are permanent records only; missing a day never removes progress.")
        for name, detail, earned in achievements:
            frame = tk.Frame(popup, bg="#173628" if earned else "#0c253b")
            frame.pack(fill="x", padx=18, pady=2)
            tk.Label(frame, text=name, anchor="w", width=18,
                     fg="#72e4aa" if earned else "#7890a3", bg=frame["bg"],
                     font=("Segoe UI", 8, "bold")).pack(side="left", padx=8, pady=6)
            tk.Label(frame, text=detail, anchor="w", fg=TEXT if earned else "#6e8192",
                     bg=frame["bg"], font=("Segoe UI", 8)).pack(side="left")

    def select_specialisation(self, value):
        if not self.state["specialisation"] and self.state["total_focus_seconds"] >= 1800:
            self.state["specialisation"] = value
            self.event = f"Career selected: {value.title()}."
            self.save()
            self.open_career()

    def select_helmet(self, color):
        choices = ("#ffdb4d", "#69ddff", "#6be6a0", "#c98bff", "#ff8a65")
        if color in choices and self.state["total_focus_seconds"] >= choices.index(color) * 3600:
            self.state["helmet_color"] = color
            self.save()
            self.open_career()

    def open_settings(self):
        popup = self.new_popup("Settings", 430, 550)
        self.popup_title(popup, "SETTINGS & SAVE", "Accessibility and performance options apply immediately.")
        sound = tk.BooleanVar(value=self.state["sound_enabled"])
        motion = tk.BooleanVar(value=self.state["reduced_motion"])
        topmost = tk.BooleanVar(value=self.state["always_on_top"])
        fps = tk.IntVar(value=self.state["target_fps"])
        daily_goal = tk.IntVar(value=self.state["daily_goal"] // 60)

        def apply_settings():
            self.state["sound_enabled"] = sound.get()
            self.state["reduced_motion"] = motion.get()
            self.state["always_on_top"] = topmost.get()
            self.state["target_fps"] = fps.get()
            self.state["daily_goal"] = daily_goal.get() * 60
            self.root.attributes("-topmost", topmost.get())
            popup.attributes("-topmost", topmost.get())
            self.save()
            self.event = "Settings saved."

        for text, variable in (("Session completion sound", sound),
                               ("Reduced motion and no camera shake", motion),
                               ("Keep game above other windows", topmost)):
            tk.Checkbutton(popup, text=text, variable=variable, command=apply_settings,
                           selectcolor="#173650", fg=TEXT, bg=BG, activebackground=BG,
                           activeforeground=TEXT, font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=6)
        tk.Label(popup, text="ANIMATION RATE", fg=MUTED, bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=18, pady=(12, 3))
        for value in (30, 60):
            tk.Radiobutton(popup, text=f"{value} FPS", variable=fps, value=value,
                           command=apply_settings, selectcolor="#173650", fg=TEXT, bg=BG,
                           activebackground=BG, activeforeground=TEXT,
                           font=("Segoe UI", 9)).pack(anchor="w", padx=22)
        tk.Label(popup, text="DAILY FOCUS GOAL", fg=MUTED, bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=18, pady=(10, 3))
        goal_row = tk.Frame(popup, bg=BG)
        goal_row.pack(anchor="w", padx=18)
        for minutes in (30, 60, 120):
            tk.Radiobutton(goal_row, text=f"{minutes}m", variable=daily_goal, value=minutes,
                           command=apply_settings, indicatoron=False, selectcolor="#25895f",
                           fg=TEXT, bg="#173650", activebackground="#285b82",
                           activeforeground=TEXT, relief="flat", width=8, pady=5,
                           font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 5))
        self.menu_action(popup, "EXPORT SAVE BACKUP", self.export_save, "#315f78")
        self.menu_action(popup, "IMPORT SAVE BACKUP", self.import_save, "#594c70")

    def export_save(self):
        self.save()
        destination = filedialog.asksaveasfilename(parent=self.root, title="Export Focus Miner save",
                                                   defaultextension=".json", filetypes=(("JSON save", "*.json"),))
        if destination:
            try:
                shutil.copy2(self.store.path, destination)
                self.event = "Save backup exported."
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"Could not export the save:\n{exc}", parent=self.root)

    def import_save(self):
        source = filedialog.askopenfilename(parent=self.root, title="Import Focus Miner save",
                                            filetypes=(("JSON save", "*.json"),))
        if not source or not messagebox.askyesno(APP_NAME, "Replace current progress with this backup?", parent=self.root):
            return
        try:
            json.loads(Path(source).read_text(encoding="utf-8"))
            self.store.folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, self.store.path)
            messagebox.showinfo(APP_NAME, "Save imported. Restart Focus Miner to load it.", parent=self.root)
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_NAME, f"That backup could not be imported:\n{exc}", parent=self.root)

    @staticmethod
    def stage_for(total):
        return sum(total >= threshold for threshold, _ in MILESTONES)

    def settlement_count(self):
        total = self.state["total_focus_seconds"]
        return sum(total >= item["unlock"] for item in SETTLEMENT_BUILDINGS)

    def building_progress(self, building):
        """Return 0..1 construction progress, beginning before completion."""
        unlock = building["unlock"]
        start = max(0, unlock * .80)
        return max(0.0, min(1.0, (self.state["total_focus_seconds"] - start)
                                / max(1, unlock - start)))

    def settlement_bonus(self, key):
        return self.state["total_focus_seconds"] >= next(
            item["unlock"] for item in SETTLEMENT_BUILDINGS if item["key"] == key)

    def unlocked_regions(self):
        total = self.state["total_focus_seconds"]
        return [region for region in REGIONS if total >= region["unlock"]]

    def next_milestone(self):
        total = self.state["total_focus_seconds"]
        return next(((seconds, title) for seconds, title in MILESTONES if total < seconds), None)

    def rank_name(self):
        total = self.state["total_focus_seconds"]
        return max((item for item in RANKS if total >= item[0]),
                   key=lambda item: item[0])[1]

    def contract_target(self):
        """Gradually longer contracts, capped so progress always stays visible."""
        return 4 + min(8, self.state["contracts_completed"] // 2)

    def advance_contract(self):
        """Complete deterministic trip contracts and return any earned supplies."""
        self.state["contract_progress"] += 1
        target = self.contract_target()
        if self.state["contract_progress"] < target:
            return None

        self.state["contract_progress"] -= target
        contract_number = self.state["contracts_completed"]
        unlocked_resources = [
            region["key"] for region in self.unlocked_regions()
            if region["key"] != "mixed"
        ]
        resource = unlocked_resources[contract_number % len(unlocked_resources)]
        base_rewards = {"stone": 24, "coins": 10, "diamonds": 3, "gems": 2}
        amount = base_rewards[resource] + contract_number // 3
        if self.state["specialisation"] == "merchant":
            amount = math.ceil(amount * 1.25)
        if self.settlement_bonus("workshop"):
            amount += max(1, amount // 10)
        self.state[resource] += amount
        self.state["contracts_completed"] += 1
        return resource, amount, self.state["contracts_completed"]

    def iso(self, gx, gy, z=0):
        """Project grid coordinates into a compact isometric camera."""
        kick = self.camera_kick if self.active and not self.state["reduced_motion"] else 0.0
        shake_x = math.sin(self.animation * 47) * kick
        shake_y = math.cos(self.animation * 39) * kick * .55
        local_x = gx - self.camera_position[0]
        local_y = gy - self.camera_position[1]
        camera_y = max(92, min(128, CANVAS_H * 0.45))
        return (CANVAS_W / 2 + (local_x - local_y) * 30 + shake_x,
                camera_y + (local_x + local_y) * 15 - z + shake_y)

    @staticmethod
    def tile_height(gx, gy):
        # Deterministic variation makes the platform feel carved, not flat.
        return 8 + ((gx * 7 + gy * 11) % 3) * 2

    def draw_glow(self, x, y, color, radius, strength=1.0):
        """Fake additive light with layered opaque colours supported by Canvas."""
        cave = "#0e2b43"
        for scale, amount in ((1.0, .10), (.72, .16), (.45, .25)):
            r = radius * scale
            fill = blend(cave, color, amount * strength)
            self.canvas.create_oval(x - r, y - r * .62, x + r, y + r * .62,
                                    fill=fill, outline="")

    def draw_tile(self, gx, gy):
        c = self.canvas
        height = self.tile_height(gx, gy)
        x, y = self.iso(gx, gy)
        light = ((gx * 3 + gy * 5) % 4) / 18
        top_color = blend("#3e7096", "#68a5c9", light)
        left_color = blend(top_color, "#10283c", .43)
        right_color = blend(top_color, "#173a55", .29)
        top = (x, y - 15, x + 30, y, x, y + 15, x - 30, y)
        left = (x - 30, y, x, y + 15, x, y + 15 + height, x - 30, y + height)
        right = (x + 30, y, x, y + 15, x, y + 15 + height, x + 30, y + height)
        c.create_polygon(left, fill=left_color, outline="#101822")
        c.create_polygon(right, fill=right_color, outline="#111c27")
        c.create_polygon(top, fill=top_color, outline="#79a9c7")
        # Small cracks give the top surface scale without visual clutter.
        if (gx * 5 + gy * 3) % 4 == 0:
            c.create_line(x - 9, y + 1, x - 2, y + 4, x + 4, y + 1,
                          fill=blend(top_color, "#0b1119", .38), width=1)

    def draw_background(self):
        c = self.canvas
        biome = self.current_biome()
        camera_delta = ((self.camera_position[0] - self.BASE[0])
                        - (self.camera_position[1] - self.BASE[1]))
        parallax = camera_delta * .7
        bands = biome["bands"]
        band_h = CANVAS_H / len(bands)
        for index, color in enumerate(bands):
            c.create_rectangle(0, index * band_h, CANVAS_W,
                               (index + 1) * band_h + 1, fill=color, outline="")

        # Receding cavern arch and pools of coloured reflected light.
        c.create_oval(333 + parallax, -29, 535 + parallax, 122,
                      fill="#174563", outline="")
        c.create_oval(371 + parallax * 1.4, -5, 503 + parallax * 1.4, 95,
                      fill="#22617f", outline="")
        self.draw_glow(62, 61, biome["glow"], 46, .95)
        self.draw_glow(433, 118, blend(biome["glow"], "#63ffa5", .45), 55, .88)

        # A slow underground stream and distant columns differentiate biomes.
        stream = blend(biome["bands"][-1], biome["glow"], .42)
        c.create_polygon(0, CANVAS_H * .74, CANVAS_W * .23, CANVAS_H * .69,
                         CANVAS_W * .57, CANVAS_H * .77, CANVAS_W, CANVAS_H * .68,
                         CANVAS_W, CANVAS_H, 0, CANVAS_H, fill=stream, outline="")
        for x in (CANVAS_W * .18, CANVAS_W * .76):
            c.create_rectangle(x - 7, 28, x + 7, CANVAS_H * .58,
                               fill=blend(biome["bands"][2], "#05080c", .35), outline="")

        # Silhouetted stalactites create foreground/background separation.
        cave_edge = [(0, 0), (0, 24), (18, 33), (30, 72), (43, 29),
                     (71, 42), (90, 13), (118, 32), (141, 0)]
        c.create_polygon(cave_edge, fill="#080d14", outline="")
        c.create_polygon(CANVAS_W, 0, CANVAS_W, 42, 472, 31, 460, 69,
                         444, 25, 420, 39, 397, 0, fill="#080d14", outline="")
        for x, height in ((14, 23), (101, 17), (153, 12), (363, 15), (478, 27)):
            c.create_polygon(x - 9, CANVAS_H, x + 11, CANVAS_H,
                             x + 2, CANVAS_H - height, fill="#101923", outline="")

        # Dust motes drift only while the world is running; they freeze on return.
        for mote in self.ambient_dust:
            motion = 0.25 if self.state["reduced_motion"] else 1.0
            drift = math.sin(self.animation * .7 + mote["phase"]) * 4 * motion
            lift = math.cos(self.animation * .43 + mote["phase"]) * 2 * motion
            color = "#78a9c8" if mote["size"] == 1 else "#a3d8f2"
            c.create_oval(mote["x"] + drift, mote["y"] + lift,
                          mote["x"] + drift + mote["size"],
                          mote["y"] + lift + mote["size"], fill=color, outline="")

    def draw_paths(self, unlocked):
        c = self.canvas
        bx, by = self.iso(*self.BASE)
        for region in REGIONS:
            tx, ty = self.iso(*region["pos"])
            is_open = region["key"] in unlocked
            c.create_line(bx + 2, by + 5, tx + 2, ty + 5, fill="#18212b", width=5)
            c.create_line(bx, by + 2, tx, ty + 2,
                          fill=blend("#6d8aa2", region["tint"], .40) if is_open else "#40566a",
                          width=2, dash=(4, 7))
            if is_open:
                world_distance = math.hypot(region["pos"][0] - self.BASE[0],
                                            region["pos"][1] - self.BASE[1])
                path_steps = max(7, math.ceil(world_distance * 1.4))
                for step in range(1, path_steps):
                    amount = step / path_steps
                    sx = bx + (tx - bx) * amount
                    sy = by + (ty - by) * amount
                    if -8 <= sx <= CANVAS_W + 8 and -8 <= sy <= CANVAS_H + 8:
                        c.create_oval(sx - 3, sy - 1, sx + 3, sy + 2,
                                      fill="#8fb8d2", outline="#24445c")
                if self.active and self.target and region["key"] == self.target["key"]:
                    # A travelling light communicates the active route without
                    # requiring a label or distracting notification.
                    travel = (self.animation * .32) % 1.0
                    marker_x = bx + (tx - bx) * travel
                    marker_y = by + (ty - by) * travel
                    c.create_oval(marker_x - 6, marker_y - 4,
                                  marker_x + 6, marker_y + 4,
                                  fill=blend("#233342", region["tint"], .45), outline="")
                    c.create_oval(marker_x - 2, marker_y - 2,
                                  marker_x + 2, marker_y + 2,
                                  fill="#eefaff", outline=region["tint"])

        if self.stage_for(self.state["total_focus_seconds"]) >= 3:
            target = next(item for item in REGIONS if item["key"] == "gems")
            tx, ty = self.iso(*target["pos"])
            for offset in (-4, 4):
                c.create_line(bx + offset, by + 4, tx + offset, ty + 4,
                              fill="#d5bd86", width=2)
            rail_steps = max(8, math.ceil(math.hypot(
                target["pos"][0] - self.BASE[0],
                target["pos"][1] - self.BASE[1]) * 1.7))
            for step in range(rail_steps + 1):
                amount = step / rail_steps
                sx, sy = bx + (tx - bx) * amount, by + (ty - by) * amount
                if -12 <= sx <= CANVAS_W + 12 and -12 <= sy <= CANVAS_H + 12:
                    c.create_line(sx - 8, sy + 3, sx + 8, sy + 3,
                                  fill="#8b6844", width=2)

    def draw_crystal(self, x, y, color, scale=1.0):
        c = self.canvas
        dark = blend(color, "#101723", .46)
        light = blend(color, "#ffffff", .33)
        c.create_polygon(x - 7 * scale, y, x, y - 25 * scale,
                         x + 7 * scale, y, x + 2 * scale, y + 6 * scale,
                         fill=color, outline=light)
        c.create_polygon(x, y - 25 * scale, x + 7 * scale, y,
                         x + 2 * scale, y + 6 * scale, fill=dark, outline="")
        c.create_line(x - 2 * scale, y - 18 * scale, x + 1 * scale, y - 7 * scale,
                      fill=light, width=max(1, round(scale)))

    def draw_region(self, region, is_open):
        c = self.canvas
        x, y = self.iso(*region["pos"])
        color = region["tint"] if is_open else "#566270"
        if is_open:
            self.draw_glow(x, y - 11, color, 31, .8 + .12 * math.sin(self.animation * 1.6))
        if self.active and self.target and region["key"] == self.target["key"]:
            spin = (self.animation * 75) % 360
            for offset in (0, 120, 240):
                c.create_arc(x - 29, y - 22, x + 29, y + 19,
                             start=spin + offset, extent=54,
                             style="arc", outline="#dff7ff", width=2)
        c.create_oval(x - 25, y - 5, x + 25, y + 13, fill="#111922", outline="")
        c.create_polygon(x - 23, y - 5, x - 9, y - 19, x + 15, y - 16,
                         x + 23, y - 2, x + 12, y + 8, x - 15, y + 7,
                         fill="#526b82", outline="#9bb9cf")
        c.create_polygon(x - 23, y - 5, x - 9, y - 19, x - 2, y - 3,
                         x - 15, y + 7, fill="#29455d", outline="")

        if region["key"] == "coins":
            c.create_oval(x - 10, y - 25, x + 9, y - 7, fill=color, outline="#fff0b4", width=2)
            c.create_oval(x - 5, y - 21, x + 4, y - 12,
                          fill=blend(color, "#7d5211", .32), outline="")
        elif region["key"] == "mixed":
            c.create_rectangle(x - 17, y - 35, x - 9, y + 1,
                               fill=blend(color, "#16202c", .3), outline="#d8c9ff")
            c.create_rectangle(x + 9, y - 35, x + 17, y + 1,
                               fill=blend(color, "#16202c", .3), outline="#d8c9ff")
            c.create_polygon(x - 20, y - 35, x, y - 45, x + 20, y - 35,
                             fill=color, outline="#e7dcff")
            c.create_oval(x - 4, y - 17, x + 4, y - 8, fill="#ffe080", outline="")
        else:
            self.draw_crystal(x, y - 6, color, 1.0)
            self.draw_crystal(x - 13, y - 1, blend(color, "#ffffff", .12), .58)
            self.draw_crystal(x + 14, y, blend(color, "#ffffff", .20), .47)

        if not is_open:
            c.create_line(x - 20, y - 22, x + 20, y + 2, fill="#26394b", width=3)
            c.create_line(x + 20, y - 22, x - 20, y + 2, fill="#26394b", width=3)
        label = region["name"].upper() if is_open else f"LOCKED  {fmt_time(region['unlock'])}"
        c.create_rectangle(x - 31, y + 15, x + 31, y + 29,
                           fill="#0a2135", outline="#4e7795")
        c.create_text(x, y + 22, text=label, fill=color if is_open else "#718095",
                      font=("Segoe UI", 6, "bold"))

    def draw_lantern(self, x, y, flicker=0.0, glow=True):
        c = self.canvas
        strength = .82 + .12 * math.sin(self.animation * 5.2 + flicker)
        if glow:
            self.draw_glow(x, y, "#ffd36c", 26, strength)
        c.create_line(x, y + 3, x, y + 24, fill="#6e5639", width=3)
        c.create_rectangle(x - 4, y - 5, x + 4, y + 5,
                           fill="#ffd76c", outline="#fff0ae")
        c.create_line(x - 5, y - 6, x + 5, y - 6, fill="#3b3329", width=2)

    def draw_base(self, stage):
        c = self.canvas
        x, y = self.iso(*self.BASE)
        # Light pools belong behind the building geometry so opaque Canvas
        # layers read as illumination instead of covering the structure.
        if stage >= 1:
            self.draw_glow(x - 51, y + 2, "#ffd36c", 26,
                           .82 + .12 * math.sin(self.animation * 5.2 + .2))
            self.draw_glow(x + 51, y + 2, "#ffd36c", 26,
                           .82 + .12 * math.sin(self.animation * 5.2 + 1.7))
        if stage >= 4:
            self.draw_glow(x - 31, y - 5, "#60d9ff", 20, .7)
        # Raised stone foundation reinforces the isometric volume.
        c.create_polygon(x - 53, y + 8, x, y - 18, x + 54, y + 8, x, y + 35,
                         fill="#6d88a2", outline="#b8d5e9")
        c.create_polygon(x - 53, y + 8, x, y + 35, x, y + 43, x - 53, y + 16,
                         fill="#28465d", outline="#102a3b")
        c.create_polygon(x + 54, y + 8, x, y + 35, x, y + 43, x + 54, y + 16,
                         fill="#365d76", outline="#102a3b")

        width = min(45, 30 + stage * 3)
        wall_top = y - 8
        c.create_polygon(x - width, wall_top, x, wall_top + 20,
                         x, wall_top + 58, x - width, wall_top + 38,
                         fill="#9c5731" if stage < 4 else "#557a9a", outline="#3c291f")
        c.create_polygon(x + width, wall_top, x, wall_top + 20,
                         x, wall_top + 58, x + width, wall_top + 38,
                         fill="#c4753b" if stage < 4 else "#72a0c3", outline="#3c291f")
        roof = "#f09a49" if stage < 4 else "#86b6d8"
        c.create_polygon(x - width - 5, wall_top, x, wall_top - 29,
                         x, wall_top + 20, fill=blend(roof, "#151d27", .18), outline="#dfb078")
        c.create_polygon(x, wall_top - 29, x + width + 5, wall_top,
                         x, wall_top + 20, fill=roof, outline="#dfb078")
        c.create_polygon(x - 7, wall_top + 40, x, wall_top + 36,
                         x + 9, wall_top + 40, x, wall_top + 58,
                         fill="#2b211d", outline="#e1a260")
        c.create_polygon(x + 19, wall_top + 30, x + 31, wall_top + 24,
                         x + 31, wall_top + 35, x + 19, wall_top + 41,
                         fill="#55ddff", outline="#e4fbff")

        if stage >= 1:
            self.draw_lantern(x - 51, y + 2, .2, glow=False)
            self.draw_lantern(x + 51, y + 2, 1.7, glow=False)
        if stage >= 2:
            # Tiny helper, shaded as a proper isometric character.
            c.create_oval(x + 34, y + 13, x + 45, y + 24,
                          fill="#e2b58c", outline="#7b5a40")
            c.create_polygon(x + 32, y + 23, x + 44, y + 17,
                             x + 44, y + 35, x + 32, y + 41,
                             fill="#4779bd", outline="#27466c")
            c.create_arc(x + 32, y + 8, x + 46, y + 20, start=0, extent=180,
                         fill="#f0bf3e", outline="#af8217")
        if stage >= 3:
            target = next(item for item in REGIONS if item["key"] == "gems")
            progress = (self.animation * .08) % 1 if self.active else .18
            tx, ty = self.iso(*target["pos"])
            cart_x = x + (tx - x) * progress
            cart_y = y + (ty - y) * progress
            c.create_oval(cart_x - 10, cart_y + 6, cart_x - 3, cart_y + 13,
                          fill="#17202a", outline="#82909d")
            c.create_oval(cart_x + 4, cart_y + 6, cart_x + 11, cart_y + 13,
                          fill="#17202a", outline="#82909d")
            c.create_polygon(cart_x - 13, cart_y - 6, cart_x + 13, cart_y - 6,
                             cart_x + 9, cart_y + 8, cart_x - 9, cart_y + 8,
                             fill="#8c5e3c", outline="#d2a477")
        if stage >= 4:
            c.create_oval(x - 39, y - 13, x - 22, y + 4,
                          fill="#63cdf2", outline="#d4f7ff")
            c.create_line(x - 31, y - 13, x - 31, y + 4, fill="#e8fbff")
        if stage >= 5:
            c.create_line(x + 44, y - 15, x + 44, y - 61,
                          fill="#9fb0c0", width=4)
            c.create_line(x + 44, y - 59, x + 75, y - 42,
                          fill="#9fb0c0", width=3)
            c.create_line(x + 74, y - 43, x + 74, y - 20,
                          fill="#c6d1dc", width=1)
            c.create_rectangle(x + 68, y - 22, x + 80, y - 14,
                               fill="#8a603e", outline="#d7ae7e")
        if stage >= 6:
            for dx, dy in ((-69, 38), (67, 37), (-76, 17)):
                c.create_polygon(x + dx - 11, y + dy, x + dx, y + dy - 8,
                                 x + dx + 11, y + dy, x + dx, y + dy + 8,
                                 fill="#536a7e", outline="#93aabc")
        names = ("CAMP", "LIT CAMP", "CREW CAMP", "RAIL CAMP",
                 "WORKSHOP", "OUTPOST", "SETTLEMENT")
        c.create_text(x, y + 57, text=names[stage], fill=TEXT,
                      font=("Segoe UI", 7, "bold"))

    def draw_settlement_building(self, building, progress):
        """Draw a foundation, rising construction, then a distinct finished building."""
        if progress <= 0:
            return
        c = self.canvas
        x, y = self.iso(*building["pos"])
        color = building["color"]
        height = 10 + 27 * min(1.0, progress / .72)
        half = 16 if building["key"] not in ("hall", "station") else 22
        # Foundation and isometric walls.
        c.create_polygon(x - half - 4, y, x, y - 9, x + half + 4, y,
                         x, y + 10, fill="#64798a", outline="#adc2cf")
        c.create_polygon(x - half, y - 2, x, y + 7, x, y + 7 - height,
                         x - half, y - 11 - height, fill=blend(color, "#111923", .42), outline="#263544")
        c.create_polygon(x + half, y - 2, x, y + 7, x, y + 7 - height,
                         x + half, y - 11 - height, fill=blend(color, "#ffffff", .05), outline="#263544")

        if progress < 1:
            # Scaffolding makes automatic construction obvious at a glance.
            for dx in (-half - 5, half + 5):
                c.create_line(x + dx, y + 4, x + dx, y - height - 18,
                              fill="#d2b071", width=2)
            c.create_line(x - half - 7, y - height * .55, x + half + 7, y - height * .55,
                          fill="#d2b071", width=2)
            c.create_text(x, y + 18, text=f"BUILDING {round(progress * 100)}%",
                          fill="#ffd779", font=("Segoe UI", 6, "bold"))
            return

        key = building["key"]
        roof_y = y - 11 - height
        if key in ("drill", "tower", "monument"):
            mast_height = 36 if key != "tower" else 54
            c.create_line(x, roof_y, x, roof_y - mast_height, fill=color, width=5)
            c.create_polygon(x - 13, roof_y - mast_height + 8, x, roof_y - mast_height - 8,
                             x + 13, roof_y - mast_height + 8, fill=color, outline="#e8f4ff")
            if key == "drill" and self.active:
                spin = math.sin(self.animation * 7) * 7
                c.create_line(x - 15, roof_y - mast_height + spin,
                              x + 15, roof_y - mast_height - spin, fill="#eef7ff", width=2)
        else:
            c.create_polygon(x - half - 3, roof_y, x, roof_y - 17,
                             x + half + 3, roof_y, x, roof_y + 11,
                             fill=color, outline=blend(color, "#ffffff", .45))
            c.create_rectangle(x - 4, y - 17, x + 5, y + 5,
                               fill="#1e2430", outline="#e5ae69")
            c.create_rectangle(x + 7, y - 23, x + 13, y - 15,
                               fill="#66d9ff", outline="#d9f7ff")
        if self.window_size == "large":
            c.create_text(x, y + 18, text=building["name"].upper(), fill="#dcefff",
                          font=("Segoe UI", 6, "bold"))

    def draw_settlement_worker(self, index, building):
        """Small ambient workers make completed districts feel inhabited."""
        if self.state["reduced_motion"]:
            amount = .35
        else:
            amount = (.5 + .5 * math.sin(self.animation * .55 + index * 1.8))
        gx = self.BASE[0] + (building["pos"][0] - self.BASE[0]) * amount
        gy = self.BASE[1] + (building["pos"][1] - self.BASE[1]) * amount
        x, y = self.iso(gx, gy)
        c = self.canvas
        c.create_oval(x - 3, y - 12, x + 3, y - 6, fill="#dab08a", outline="#6b4b35")
        c.create_rectangle(x - 4, y - 6, x + 4, y + 5,
                           fill=("#55aee6", "#7ed18d", "#d79065")[index % 3], outline="#22394b")

    def draw_miner(self):
        c = self.canvas
        x, y = self.iso(*self.position)
        walking = self.phase.startswith("walking") and self.active
        bob = math.sin(self.animation * 7.5) * (2.0 if walking else .55)
        step = math.sin(self.animation * 9.5) * (4.2 if walking else 0)
        body = y - 19 + bob
        lamp_x, lamp_y = x + 1, body - 18
        self.draw_glow(lamp_x, lamp_y, "#dff7ff", 14, .65)
        c.create_oval(x - 14, y + 3, x + 14, y + 12,
                      fill="#091018", outline="")
        # Back limbs are darker; front limbs remain highlighted.
        c.create_line(x + 4, body + 16, x + 8 + step, body + 31,
                      fill="#52657a", width=5)
        c.create_line(x + 5, body + 1, x + 14, body + 10,
                      fill="#617489", width=4)
        c.create_polygon(x - 9, body - 4, x + 8, body - 4,
                         x + 10, body + 17, x - 8, body + 17,
                         fill="#35a8ff", outline="#0b5b9c")
        c.create_polygon(x + 3, body - 4, x + 10, body, x + 10, body + 17,
                         x + 3, body + 13, fill="#1674bd", outline="")
        c.create_polygon(x + 9, body + 1, x + 16, body + 5,
                         x + 16, body + 18, x + 9, body + 15,
                         fill="#a05be3", outline="#ead4ff")
        c.create_oval(x - 8, body - 19, x + 8, body - 4,
                      fill="#e4b68b", outline="#8b6648")
        helmet = self.state["helmet_color"]
        c.create_arc(x - 11, body - 25, x + 11, body - 9, start=0, extent=180,
                     fill=helmet, outline=blend(helmet, "#101820", .35))
        c.create_rectangle(x - 12, body - 18, x + 12, body - 14,
                           fill=helmet, outline=blend(helmet, "#101820", .35))
        c.create_oval(lamp_x - 3, lamp_y - 3, lamp_x + 3, lamp_y + 3,
                      fill="#e8fbff", outline="#69c8ed")
        c.create_line(x - 4, body + 16, x - 8 - step, body + 31,
                      fill="#c7d3df", width=5)
        c.create_line(x - 5, body + 1, x - 13, body + 10,
                      fill="#c7d3df", width=4)
        if self.phase == "mining" and self.active:
            swing = math.sin(self.animation * 10.5)
            tip_x = x - 26 - swing * 8
            tip_y = body - 11 - abs(swing) * 10
            c.create_line(x - 8, body + 3, tip_x, tip_y,
                          fill="#895b39", width=3)
            c.create_line(tip_x - 8, tip_y - 4, tip_x + 8, tip_y + 4,
                          fill="#d3dee8", width=3)
        if self.phase == "walking_back" and sum(self.cargo.values()):
            c.create_oval(x + 11, body - 5, x + 28, body + 13,
                          fill="#7946bd", outline="#e5d8ff")
            c.create_text(x + 19, body + 4, text=sum(self.cargo.values()),
                          fill="white", font=("Segoe UI", 7, "bold"))

    def draw_effects(self):
        c = self.canvas
        for ring in self.impact_rings:
            radius = ring["radius"]
            c.create_oval(ring["x"] - radius, ring["y"] - radius * .55,
                          ring["x"] + radius, ring["y"] + radius * .55,
                          outline=ring["color"], width=2)
        for particle in self.particles:
            size = particle["size"]
            color = particle["color"]
            c.create_oval(particle["x"] - size, particle["y"] - size,
                          particle["x"] + size, particle["y"] + size,
                          fill=color, outline="")
        for floater in self.floaters:
            c.create_text(floater["x"], floater["y"], text=floater["text"],
                          fill=floater["color"], font=("Segoe UI", 8, "bold"))

    def draw_overlays(self):
        c = self.canvas
        now = time.monotonic()
        centre = CANVAS_W / 2
        if now < self.banner_until:
            side = max(18, min(72, CANVAS_W * 0.15))
            c.create_rectangle(side, 8, CANVAS_W - side, 40,
                               fill="#0b1522", outline=ACCENT, width=2)
            c.create_line(side + 10, 35, CANVAS_W - side - 10, 35,
                          fill="#285f8e", width=1)
            banner_text = ("20s idle — expedition started"
                           if self.window_size == "small"
                           else "20 seconds idle — expedition started")
            c.create_text(centre, 23, text=banner_text, fill=TEXT,
                          font=("Segoe UI", 8 if self.window_size == "small" else 10, "bold"))
        if now < self.report_until:
            report_side = max(14, min(42, CANVAS_W * 0.085))
            report_bottom = min(CANVAS_H - 8, 224)
            c.create_rectangle(report_side, 55, CANVAS_W - report_side, report_bottom,
                               fill="#09131f", outline="#5db8fa", width=2)
            c.create_rectangle(report_side + 7, 62, CANVAS_W - report_side - 7, report_bottom - 7,
                               fill="#101e2d", outline="#263d52")
            c.create_text(centre, 81, text="EXPEDITION COMPLETE",
                          fill="#8ed1ff",
                          font=("Segoe UI", 9 if self.window_size == "small" else 11, "bold"))
            c.create_line(report_side + 45, 98, CANVAS_W - report_side - 45, 98,
                          fill="#31516b", width=1)
            report_y = 143 if self.window_size == "small" else 151
            c.create_text(centre, report_y, text="\n".join(self.report), fill=TEXT,
                          font=("Segoe UI", 7 if self.window_size == "small" else 9),
                          justify="center", width=max(190, CANVAS_W - 84))
            resume_text = ("Resumes after 20s away" if self.window_size == "small"
                           else "Mining resumes after 20 seconds away")
            c.create_text(centre, min(report_bottom - 17, 207), text=resume_text,
                          fill="#8295aa", font=("Segoe UI", 7))

    def draw_world(self):
        c = self.canvas
        c.delete("all")
        self.draw_background()
        for gy in range(WORLD_MIN_Y, WORLD_MAX_Y + 1):
            for gx in range(WORLD_MIN_X, WORLD_MAX_X + 1):
                tile_x, tile_y = self.iso(gx, gy)
                if (-10 <= tile_x <= CANVAS_W + 10
                        and -5 <= tile_y <= CANVAS_H + 5):
                    self.draw_tile(gx, gy)

        unlocked = {region["key"] for region in self.unlocked_regions()}
        self.draw_paths(unlocked)

        # Painter's ordering is the key 3D improvement: objects lower on the
        # isometric grid are drawn later and correctly cover objects behind.
        entities = []
        for region in REGIONS:
            screen_x, screen_y = self.iso(*region["pos"])
            if (-100 <= screen_x <= CANVAS_W + 100
                    and -100 <= screen_y <= CANVAS_H + 100):
                entities.append((sum(region["pos"]),
                                 lambda item=region: self.draw_region(
                                     item, item["key"] in unlocked)))
        base_x, base_y = self.iso(*self.BASE)
        if (-130 <= base_x <= CANVAS_W + 130
                and -130 <= base_y <= CANVAS_H + 130):
            entities.append((sum(self.BASE),
                             lambda: self.draw_base(
                                 self.stage_for(self.state["total_focus_seconds"]))))
        completed_buildings = []
        for building in SETTLEMENT_BUILDINGS:
            progress = self.building_progress(building)
            if progress <= 0:
                continue
            screen_x, screen_y = self.iso(*building["pos"])
            if (-130 <= screen_x <= CANVAS_W + 130
                    and -150 <= screen_y <= CANVAS_H + 130):
                entities.append((sum(building["pos"]),
                                 lambda item=building, value=progress:
                                 self.draw_settlement_building(item, value)))
            if progress >= 1:
                completed_buildings.append(building)
        for index, building in enumerate(completed_buildings[:3]):
            entities.append((sum(self.BASE) + index * .02,
                             lambda number=index, item=building:
                             self.draw_settlement_worker(number, item)))
        entities.append((sum(self.position) + .01, self.draw_miner))
        for _, draw_entity in sorted(entities, key=lambda item: item[0]):
            draw_entity()
        self.draw_effects()
        self.draw_overlays()

    def walking_speed(self):
        speed = 0.66 + 0.075 * (self.state["boots_level"] - 1)
        if self.state["specialisation"] == "explorer":
            speed *= 1.20
        if self.settlement_bonus("station"):
            speed *= 1.10
        return min(2.15, speed)

    def mining_duration(self):
        duration = 3.5 - 0.17 * (self.state["pickaxe_level"] - 1)
        if self.state["specialisation"] == "engineer":
            duration *= .85
        if self.settlement_bonus("drill"):
            duration *= .90
        return max(.85, duration)

    def capacity(self):
        capacity = 5 + 2 * (self.state["bag_level"] - 1)
        if self.settlement_bonus("store"):
            capacity += 3
        return min(90, capacity)

    def costs(self):
        return {
            "pickaxe": ("diamonds", 8 + int(7 * self.state["pickaxe_level"] ** 1.42)),
            "luck": ("gems", 4 + int(5 * self.state["luck_level"] ** 1.42)),
            "boots": ("coins", 12 + int(9 * self.state["boots_level"] ** 1.38)),
            "bag": ("stone", 30 + int(22 * self.state["bag_level"] ** 1.38)),
        }

    def choose_target(self):
        unlocked = self.unlocked_regions()
        resource_to_key = {"stone": "stone", "coins": "coins", "diamonds": "diamonds", "gems": "gems"}
        candidates = []
        keys = {region["key"] for region in unlocked}
        for _, (resource, cost) in self.costs().items():
            if resource_to_key[resource] in keys:
                candidates.append((self.state[resource] / max(1, cost), resource_to_key[resource]))
        if "mixed" in keys and random.random() < 0.15:
            key = "mixed"
        elif len(unlocked) > 1 and random.random() < 0.12:
            return random.choice(unlocked)
        else:
            key = min(candidates)[1]
        return next(region for region in unlocked if region["key"] == key)

    def make_cargo(self):
        cap = self.capacity()
        key = self.target["key"]
        cargo = {name: 0 for name in self.session_gains}

        def add(resource, amount):
            cargo[resource] += min(max(0, cap - sum(cargo.values())), max(0, amount))

        level, luck = self.state["pickaxe_level"], self.state["luck_level"]
        if key == "stone":
            add("stone", random.randint(3, 5) + level // 3)
        elif key == "coins":
            add("coins", random.randint(2, 4) + level // 5)
            add("stone", random.randint(1, 2))
        elif key == "diamonds":
            found = self.state["diamond_pity"] >= 3 or random.random() < min(0.72, 0.34 + luck * 0.018)
            if found:
                add("diamonds", 1 + level // 8)
                self.state["diamond_pity"] = 0
            else:
                self.state["diamond_pity"] += 1
            add("stone", random.randint(2, 4))
        elif key == "gems":
            found = self.state["gem_pity"] >= 5 or random.random() < min(0.55, 0.22 + luck * 0.014)
            if found:
                add("gems", 1)
                self.state["gem_pity"] = 0
            else:
                self.state["gem_pity"] += 1
            add("stone", random.randint(2, 3))
        else:
            add("coins", random.randint(1, 3))
            add("diamonds", int(random.random() < 0.4))
            add("gems", int(random.random() < 0.22))
            add("stone", cap)
        return cargo

    def spawn_mining_particles(self):
        """Emit small rock chips from the active landmark."""
        if not self.target:
            return
        x, y = self.iso(*self.target["pos"])
        self.camera_kick = max(self.camera_kick, 1.35)
        self.impact_rings.append({
            "x": x, "y": y - 9, "radius": 3.0, "life": .28,
            "color": blend(self.target["tint"], "#ffffff", .38),
        })
        for _ in range(3 if self.state["reduced_motion"] else 6):
            self.particles.append({
                "x": x + random.uniform(-8, 8),
                "y": y - random.uniform(7, 17),
                "vx": random.uniform(-24, 24),
                "vy": random.uniform(-35, -12),
                "life": random.uniform(.38, .75),
                "size": random.uniform(1.3, 3.0),
                "color": random.choice(("#b8c4cf", "#82909d", self.target["tint"])),
            })
        self.particles = self.particles[-120:]

    def spawn_deposit_effect(self, cargo):
        """Show deposited resources without asking the player to click."""
        x, y = self.iso(*self.BASE)
        total = sum(cargo.values())
        self.camera_kick = max(self.camera_kick, 1.8)
        self.impact_rings.append({
            "x": x, "y": y + 4, "radius": 7.0, "life": .55,
            "color": "#9ee2ff",
        })
        self.floaters.append({
            "x": x,
            "y": y - 24,
            "life": 1.8,
            "text": f"CARGO +{total}",
            "color": "#9ee2ff",
        })
        colors = {"stone": "#b7c1cc", "coins": "#ffd36a",
                  "diamonds": "#69ddff", "gems": "#6be6a0"}
        for resource, amount in cargo.items():
            for _ in range(min(5, amount)):
                self.particles.append({
                    "x": x + random.uniform(-16, 16),
                    "y": y + random.uniform(-2, 8),
                    "vx": random.uniform(-28, 28),
                    "vy": random.uniform(-42, -20),
                    "life": random.uniform(.55, 1.0),
                    "size": random.uniform(1.5, 3.3),
                    "color": colors[resource],
                })
        self.particles = self.particles[-120:]

    def update_visual_effects(self, dt):
        self.camera_kick *= math.exp(-9 * dt)
        for particle in self.particles:
            particle["x"] += particle["vx"] * dt
            particle["y"] += particle["vy"] * dt
            particle["vy"] += 58 * dt
            particle["life"] -= dt
        self.particles = [particle for particle in self.particles if particle["life"] > 0]
        for floater in self.floaters:
            floater["y"] -= 12 * dt
            floater["life"] -= dt
        self.floaters = [floater for floater in self.floaters if floater["life"] > 0]
        for ring in self.impact_rings:
            ring["radius"] += 46 * dt
            ring["life"] -= dt
        self.impact_rings = [ring for ring in self.impact_rings if ring["life"] > 0]

    def auto_upgrade(self):
        labels = {"pickaxe": "Pickaxe", "luck": "Survey", "boots": "Boots", "bag": "Backpack"}
        level_keys = {name: f"{name}_level" for name in labels}
        upgraded = []
        for _ in range(100):
            changed = False
            for name, (resource, cost) in self.costs().items():
                if self.state[resource] >= cost:
                    self.state[resource] -= cost
                    self.state[level_keys[name]] += 1
                    upgraded.append(f"{labels[name]} Lv {self.state[level_keys[name]]}")
                    changed = True
            if not changed:
                break
        return upgraded

    def apply_dynamic_event(self, cargo):
        """Occasional automatic variety; every event is helpful and needs no click."""
        chance = .13 if self.settlement_bonus("tower") else .09
        if random.random() >= chance:
            return None
        title, kind = random.choice(EVENTS)
        if kind == "cargo":
            for resource in cargo:
                cargo[resource] += math.ceil(cargo[resource] * .30)
        elif kind == "coins":
            cargo["coins"] += 2 + self.state["contracts_completed"] // 4
        elif kind == "time":
            cargo["stone"] += 3
        elif kind == "discovery":
            self.state["discovery_pity"] += 3
        elif kind == "stone":
            cargo["stone"] += 5 + self.state["pickaxe_level"] // 2
        self.state["events_seen"] += 1
        self.active_event, self.active_event_until = title, time.monotonic() + 5
        return title

    def try_discovery(self):
        available = [name for name in DISCOVERIES[self.target["key"]]
                     if name not in self.state["discoveries"]]
        if not available:
            return None
        chance = min(0.24, 0.045 + self.state["luck_level"] * 0.006)
        if self.state["specialisation"] == "collector":
            chance += .035
        if self.settlement_bonus("lab"):
            chance += .015
        if self.state["discovery_pity"] >= 11 or random.random() < chance:
            found = random.choice(available)
            self.state["discoveries"].append(found)
            self.state["discovery_pity"] = 0
            return found
        self.state["discovery_pity"] += 1
        return None

    def move(self, target, amount):
        dx, dy = target[0] - self.position[0], target[1] - self.position[1]
        distance = math.hypot(dx, dy)
        if distance <= amount or distance == 0:
            self.position[:] = target
            return True
        self.position[0] += dx / distance * amount
        self.position[1] += dy / distance * amount
        return False

    @staticmethod
    def distance_between(first, second):
        return math.hypot(first[0] - second[0], first[1] - second[1])

    def update_camera(self, dt):
        """Smoothly follow the miner and look slightly toward its destination."""
        destination = self.position
        look_ahead = 0.0
        if self.target:
            if self.phase == "walking_out":
                destination, look_ahead = self.target["pos"], .20
            elif self.phase == "walking_back":
                destination, look_ahead = self.BASE, .20
            elif self.phase == "mining":
                destination, look_ahead = self.target["pos"], .08

        desired_x = self.position[0] * (1 - look_ahead) + destination[0] * look_ahead
        desired_y = self.position[1] * (1 - look_ahead) + destination[1] * look_ahead
        smoothing = 1 - math.exp(-2.1 * dt)
        self.camera_position[0] += (desired_x - self.camera_position[0]) * smoothing
        self.camera_position[1] += (desired_y - self.camera_position[1]) * smoothing

    def update_game(self, real_dt):
        # The multiplier accelerates the simulation only. Focus totals and
        # countdowns remain trustworthy measures of real elapsed time.
        sim_dt = min(.5, real_dt * self.game_speed)
        before = self.state["total_focus_seconds"]
        self.state["total_focus_seconds"] += real_dt
        self.session_time += real_dt
        self.animation += sim_dt
        self.phase_time += sim_dt
        self.update_visual_effects(sim_dt)
        if self.phase == "mining":
            burst = int(self.phase_time * 4)
            if burst != self.last_mining_burst:
                self.last_mining_burst = burst
                self.spawn_mining_particles()
        for seconds, title in MILESTONES:
            if before < seconds <= self.state["total_focus_seconds"]:
                self.session_unlocks.append(title)
                self.event = f"Milestone unlocked: {title}."
        for building in SETTLEMENT_BUILDINGS:
            if before < building["unlock"] <= self.state["total_focus_seconds"]:
                self.session_buildings.append(building["name"])
                self.event = f"Settlement completed: {building['name']}."
        for biome in BIOMES:
            if before < biome["unlock"] <= self.state["total_focus_seconds"] and biome["unlock"]:
                self.session_unlocks.append(biome["name"])
                self.event = f"New expedition destination: {biome['name']}."

        if self.phase == "waiting":
            self.target = self.choose_target()
            self.phase, self.phase_time = "walking_out", 0.0
            self.event = f"Heading to {self.target['name']}."
        elif self.phase == "walking_out" and self.move(self.target["pos"], self.walking_speed() * sim_dt):
            self.phase, self.phase_time = "mining", 0.0
            self.last_mining_burst = -1
            self.event = f"Mining at {self.target['name']}."
        elif self.phase == "mining" and self.phase_time >= self.mining_duration():
            self.cargo = self.make_cargo()
            self.phase, self.phase_time = "walking_back", 0.0
            self.event = "Cargo secured. Returning to base."
        elif self.phase == "walking_back" and self.move(self.BASE, self.walking_speed() * sim_dt):
            event_title = self.apply_dynamic_event(self.cargo)
            deposited_cargo = self.cargo.copy()
            for resource, amount in self.cargo.items():
                self.state[resource] += amount
                self.session_gains[resource] += amount
            self.state["trips"] += 1
            self.session_trips += 1
            contract = self.advance_contract()
            if contract:
                resource, amount, contract_number = contract
                self.session_gains[resource] += amount
                self.session_contracts += 1
            found = self.try_discovery()
            upgrades = self.auto_upgrade()
            self.session_upgrades.extend(upgrades)
            if found:
                self.session_finds.append(found)
                self.event = f"Museum discovery: {found}!"
            elif event_title:
                self.event = f"Expedition event: {event_title}."
            elif contract:
                self.event = (f"Supply Contract {contract_number} complete: "
                              f"+{amount} {resource}.")
            elif upgrades:
                self.event = "Built: " + ", ".join(upgrades[-2:])
            else:
                gained = ", ".join(f"{amount} {name}" for name, amount in self.cargo.items() if amount)
                self.event = "Stored " + gained
            self.spawn_deposit_effect(deposited_cargo)
            self.cargo = {key: 0 for key in self.cargo}
            self.phase, self.phase_time = "waiting", 0.0
        self.update_camera(real_dt)

    def begin_session(self, now):
        self.active = True
        self.session_time = 0.0
        self.session_trips = 0
        self.session_gains = {key: 0 for key in self.session_gains}
        self.session_upgrades, self.session_unlocks, self.session_finds = [], [], []
        self.session_buildings = []
        self.session_contracts = 0
        self.banner_until, self.report_until = now + 3, 0
        if self.run_mode == "focus":
            self.event = f"Focus session started: {self.state['focus_task']}."
        else:
            self.event = ("Task-mode expedition started."
                          if self.run_mode == "task" else "Focus expedition started.")

    def update_mode_button(self):
        """Keep the compact header toggle clear at a glance."""
        compact = self.window_size == "small"
        if self.run_mode == "break":
            self.mode_toggle.config(text="BREAK", bg="#a36d25", activebackground="#c58b3c")
        elif self.run_mode == "focus":
            self.mode_toggle.config(text="FOCUS", bg="#7a4eaa", activebackground="#9b68cb")
        elif self.run_mode == "task":
            self.mode_toggle.config(text="TASK" if compact else "TASK MODE",
                                    bg="#2b9b6d", activebackground="#3cc58b")
        else:
            self.mode_toggle.config(text="IDLE" if compact else "IDLE MODE",
                                    bg="#1976b8", activebackground="#259fe8")

    def update_speed_button(self):
        self.speed_button.config(text=f"x{self.game_speed}")

    def cycle_game_speed(self):
        """Cycle simulation speed without falsifying recorded focus time."""
        index = GAME_SPEEDS.index(self.game_speed)
        self.game_speed = GAME_SPEEDS[(index + 1) % len(GAME_SPEEDS)]
        self.state["game_speed"] = self.game_speed
        self.update_speed_button()
        self.event = f"Game speed set to x{self.game_speed}. Focus time remains real-time."
        self.save()

    def toggle_run_mode(self):
        if self.run_mode == "focus" and self.active:
            self.end_session(time.monotonic(), completed=False)
        self.run_mode = "task" if self.run_mode == "idle" else "idle"
        self.state["run_mode"] = self.run_mode
        self.state["focus_end_time"] = 0.0
        self.state["break_end_time"] = 0.0
        self.update_mode_button()
        if self.run_mode == "task":
            self.event = "Task Mode enabled — mining continues while you use the laptop."
        else:
            self.event = "Idle Mode enabled — mining requires 20 seconds without input."
        self.save()

    def should_mine(self, inactive):
        if self.run_mode == "break":
            return False
        if self.run_mode == "focus":
            return time.time() < self.state["focus_end_time"]
        return self.run_mode == "task" or inactive >= IDLE_SECONDS

    def end_session(self, now, completed=False):
        self.active = False
        self.particles.clear()
        self.floaters.clear()
        self.impact_rings.clear()
        self.camera_kick = 0.0
        self.state["sessions"] += 1
        self.state["longest_session"] = max(self.state["longest_session"], self.session_time)
        self.state["session_history"].append({
            "date": time.strftime("%Y-%m-%d", time.localtime()),
            "seconds": round(self.session_time, 1),
            "task": self.state["focus_task"] if self.run_mode == "focus" else "",
            "completed": bool(completed),
        })
        self.state["session_history"] = self.state["session_history"][-90:]
        gains = "  ".join(f"{name.title()} +{amount}" for name, amount in self.session_gains.items() if amount)
        self.report = [f"Focused {fmt_time(self.session_time)}  •  {self.session_trips} trips",
                       gains or "No cargo deposited yet"]
        if self.session_upgrades:
            self.report.append(f"Built {len(self.session_upgrades)} upgrades • {self.session_upgrades[-1]}")
        if self.session_contracts:
            self.report.append(f"Supply contracts completed: {self.session_contracts}")
        if self.session_unlocks:
            self.report.append("Unlocked: " + self.session_unlocks[-1])
        if self.session_finds:
            self.report.append("Discovered: " + ", ".join(self.session_finds))
        if self.session_buildings:
            self.report.append("Built: " + self.session_buildings[-1])
        self.report = self.report[:5]
        self.report_until = now + 8
        self.event = f"Expedition paused after {fmt_time(self.session_time)}."
        if completed:
            self.event = f"Focus session complete: {fmt_time(self.session_time)}."
            self.state["contract_progress"] += 1
            if self.state["sound_enabled"]:
                try:
                    self.root.bell()
                except tk.TclError:
                    pass
        self.save()

    def draw_labels(self, inactive):
        if self.active:
            place = self.target["name"] if self.target else "Preparing"
            if self.phase == "walking_out" and self.target:
                eta = math.ceil(self.distance_between(self.position, self.target["pos"])
                                / self.walking_speed())
                status = f"TO {place.upper()} • {eta}s"
            elif self.phase == "walking_back":
                eta = math.ceil(self.distance_between(self.position, self.BASE)
                                / self.walking_speed())
                status = f"TO BASE • {eta}s"
            elif self.phase == "mining":
                status = f"MINING • {place.upper()}"
            else:
                status = "PLANNING ROUTE"
            if self.run_mode == "focus":
                remaining = max(0, self.state["focus_end_time"] - time.time())
                status = f"FOCUS {fmt_time(remaining)} • {status}"
            self.mode.config(text=status, fg="#64f0aa")
        else:
            if self.run_mode == "break":
                status = f"BREAK • {fmt_time(max(0, self.state['break_end_time'] - time.time()))} LEFT"
            elif self.run_mode == "task":
                status = "TASK MODE STARTING"
            else:
                status = f"ACTIVE • MINES IN {max(0, math.ceil(IDLE_SECONDS - inactive))}s"
            self.mode.config(text=status, fg=MUTED)
        s = self.state
        if self.window_size == "small":
            self.resources.config(text=(f"St {s['stone']}  Dia {s['diamonds']}  Gem {s['gems']}  Coin {s['coins']}\n"
                                        f"Run {fmt_time(self.session_time)}  Total {fmt_time(s['total_focus_seconds'])}"))
            self.upgrades.config(text=(f"Pick {s['pickaxe_level']}  Survey {s['luck_level']}\n"
                                       f"Boot {s['boots_level']}  Bag {s['bag_level']}"))
            self.museum.config(text=f"M {len(s['discoveries'])}/10 • S {self.settlement_count()}/10")
        elif self.window_size == "medium":
            self.resources.config(text=(f"Stone {s['stone']}   Dia {s['diamonds']}\n"
                                        f"Gems {s['gems']}   Coins {s['coins']}\n"
                                        f"Run {fmt_time(self.session_time)}  Total {fmt_time(s['total_focus_seconds'])}"))
            self.upgrades.config(text=(f"Pickaxe {s['pickaxe_level']}  Survey {s['luck_level']}\n"
                                       f"Boots {s['boots_level']}  Bag {s['bag_level']}"))
            self.museum.config(text=f"Museum {len(s['discoveries'])}/10 • Settlement {self.settlement_count()}/10")
        else:
            self.resources.config(text=(f"Stone {s['stone']:>5}   Diamonds {s['diamonds']:>4}\n"
                                        f"Gems  {s['gems']:>5}   Coins    {s['coins']:>4}\n"
                                        f"Run {fmt_time(self.session_time)}   Total {fmt_time(s['total_focus_seconds'])}"))
            self.upgrades.config(text=(f"Pickaxe  Lv {s['pickaxe_level']}\nSurvey   Lv {s['luck_level']}\n"
                                       f"Boots    Lv {s['boots_level']}\nBackpack Lv {s['bag_level']}"))
            self.museum.config(text=(f"Museum {len(s['discoveries'])}/10  •  Settlement {self.settlement_count()}/10  •  "
                                     f"Contract {s['contract_progress']}/{self.contract_target()}"))
        self.event_label.config(text=self.event)
        self.rank_label.config(text=self.rank_name().upper())

        c = self.progress
        c.delete("all")
        c.create_rectangle(0, 5, CANVAS_W, 22, fill="#0b2942", outline="#3d7da6")
        milestone = self.next_milestone()
        if self.run_mode == "break":
            remaining = max(0, self.state["break_end_time"] - time.time())
            fraction = max(0.0, min(1.0, 1 - remaining / 300))
            c.create_rectangle(1, 6, 1 + (CANVAS_W - 2) * fraction, 21,
                               fill="#d69742", outline="")
            text = f"BREAK • {fmt_time(remaining)} LEFT • CLICK BREAK TO SKIP"
        elif self.run_mode == "focus" and self.active:
            remaining = max(0, self.state["focus_end_time"] - time.time())
            duration = max(1, self.state["focus_duration"])
            fraction = max(0.0, min(1.0, 1 - remaining / duration))
            c.create_rectangle(1, 6, 1 + (CANVAS_W - 2) * fraction, 21,
                               fill="#8d5dcc", outline="")
            task = self.state["focus_task"] or "FOCUSED WORK"
            text = f"{task.upper()} • {fmt_time(remaining)} LEFT"
        elif milestone:
            previous = max((seconds for seconds, _ in MILESTONES if seconds <= s["total_focus_seconds"]), default=0)
            fraction = (s["total_focus_seconds"] - previous) / (milestone[0] - previous)
            c.create_rectangle(1, 6, 1 + (CANVAS_W - 2) * fraction, 21,
                               fill="#28aef2", outline="")
            text = f"NEXT: {milestone[1].upper()} • {fmt_time(milestone[0] - s['total_focus_seconds'])}"
        else:
            target = self.contract_target()
            fraction = min(1.0, s["contract_progress"] / max(1, target))
            c.create_rectangle(1, 6, 1 + (CANVAS_W - 2) * fraction, 21,
                               fill="#a66cf4", outline="")
            text = (f"SUPPLY CONTRACT {s['contracts_completed'] + 1} • "
                    f"{s['contract_progress']}/{target} TRIPS")
        if self.window_size == "small":
            text = text.replace("SUPPLY CONTRACT", "CONTRACT").replace("NEXT: ", "NEXT ")
            if len(text) > 42:
                text = text[:41] + "…"
        c.create_text(CANVAS_W / 2, 13.5, text=text, fill=TEXT,
                      font=("Segoe UI", 6 if self.window_size == "small" else 7, "bold"))

    def save(self):
        if not self.store.save(self.state):
            self.event = self.store.error

    def loop(self):
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self.last_update))
        self.last_update = now
        try:
            inactive = idle_seconds()
        except (AttributeError, OSError):
            inactive = 0.0
            if self.run_mode == "idle":
                self.event = "Idle Mode requires Windows; Task Mode can still run."
        break_finished = (self.run_mode == "break"
                          and time.time() >= self.state["break_end_time"])
        if break_finished:
            self.run_mode = self.state["run_mode"] = "idle"
            self.state["break_end_time"] = 0.0
            self.event = "Break complete — ready for the next expedition."
            self.update_mode_button()
            if self.state["sound_enabled"]:
                try:
                    self.root.bell()
                except tk.TclError:
                    pass
        focus_finished = (self.run_mode == "focus" and self.active
                          and time.time() >= self.state["focus_end_time"])
        if focus_finished:
            self.end_session(now, completed=True)
            self.state["focus_end_time"] = 0.0
            if self.state["break_enabled"]:
                self.run_mode = self.state["run_mode"] = "break"
                self.state["break_end_time"] = time.time() + 300
                self.event = "Focus complete — five-minute break started."
            else:
                self.run_mode = self.state["run_mode"] = "idle"
            self.update_mode_button()
            self.save()
        should_run = self.should_mine(inactive)
        if should_run and not self.active:
            self.begin_session(now)
        elif not should_run and self.active:
            self.end_session(now)
        if self.active:
            self.update_game(dt)

        # Windows enters a native move/size loop while the title bar is dragged.
        # Keeping the 16 ms timer alive but skipping the expensive full Canvas
        # rebuild during that brief period leaves the DWM free to move the window
        # smoothly instead of fighting hundreds of draw calls per frame.
        visible = self.root.state() != "iconic"
        render_interval = 1 / self.state["target_fps"]
        if visible and now >= self.window_motion_until and now - self.last_render >= render_interval:
            self.last_render = now
            self.draw_world()
        if visible and now - self.last_labels >= .10:
            self.last_labels = now
            self.draw_labels(inactive)
        if now - self.last_save >= SAVE_INTERVAL:
            self.last_save = now
            self.save()
        self.root.after(max(FRAME_MS, round(1000 / self.state["target_fps"])), self.loop)

    def close(self):
        if self.active and self.session_time >= 0.5:
            self.end_session(time.monotonic(), completed=False)
        else:
            self.save()
        self.root.destroy()


def enable_high_dpi():
    """Use the sharpest Windows DPI mode available without resizing the UI."""
    try:
        # Per-monitor v2 keeps vectors and text crisp when moving between
        # displays with different scaling levels (Windows 10 1703+).
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(1) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def main():
    if os.name != "nt":
        raise SystemExit("Focus Miner uses Windows idle detection and must run on Windows.")
    enable_high_dpi()
    root = tk.Tk()
    FocusMiner(root)
    root.mainloop()


if __name__ == "__main__":
    main()
