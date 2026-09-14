"""PATCH-08 启动动画（splash）——硬约束：不延迟启动。

- 动画与数据加载**并行**：splash 只覆盖真实加载耗时（启动时长 = max(首帧, 加载)，
  不额外增加等待）；加载完成即自动关闭。
- **任意键跳过**（立即进主界面）；**非 TTY（headless/测试）自动跳过**；
  `--no-splash` / `PATCHOULI_NO_SPLASH=1` 强制关闭。
零第三方（除 Textual 本体）。
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

LOGO = r"""
   ___       _      _                 _ _
  / _ \__ _| |_ __| |_  ___ _  _| | (_)
 / /_)/ _` |  _/ _| ' \/ _ \ || | | | |
/ ___/ (_| | || (_| || |  __/\_,_|_|_|_|
\/    \__,_|\__\__|_||_|\___|   v0.1
"""

FRAMES = ("/", "-", "\\", "|")
TICK_SECONDS = 0.10


def splash_delay(elapsed: float, min_show: float) -> float:
    """需补足的等待秒数：加载快于最小展示时补齐（可感知）；加载慢则不额外等待。"""
    return max(0.0, float(min_show) - float(elapsed))


class SplashScreen(ModalScreen):
    """启动屏：logo + 旋转 + 加载状态行；任意键跳过。

    配色：半透黑底 + 淡紫填充（40K 色孽调：深紫黑/亮紫边框/淡紫文字）。
    """

    CSS = """
    SplashScreen { background: #0b060f 78%; }
    #splash-box { background: #2e1a3d 94%; border: round #c9a0e8; padding: 1 3; }
    #splash-logo { color: #e6d4f5; }
    #splash-line { color: #a37fd0; }
    """

    def __init__(self, status: str = "启动中…", **kwargs):
        super().__init__(**kwargs)
        self.status_text = status
        self._frame = 0
        self._timer = None

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="splash-box"):
                yield Static(LOGO, id="splash-logo", markup=False)
                yield Static("", id="splash-line", markup=False)

    def on_mount(self) -> None:
        self._render_line()
        self._timer = self.set_interval(TICK_SECONDS, self._tick)

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(FRAMES)
        self._render_line()

    def _render_line(self) -> None:
        spin = FRAMES[self._frame]
        try:
            self.query_one("#splash-line", Static).update(f"  [{spin}] {self.status_text}    （任意键跳过）")
        except Exception:  # noqa: BLE001 — 屏幕已卸载
            pass

    def set_status(self, text: str) -> None:
        self.status_text = text
        self._render_line()

    def on_key(self) -> None:
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()
