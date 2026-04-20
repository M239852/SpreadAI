from __future__ import annotations
import threading
from typing import Callable
import customtkinter as ctk

from ..api.odds_api import OddsAPI, SPORT_LABELS
from ..api.demo_data import demo_games
from ..utils.storage import load_config, save_config
from . import theme as T
from .state import AppState
from .games_view import GamesView
from .analysis_view import AnalysisView
from .betslip_view import BetSlipPanel
from .settings_view import SettingsView
from .generator_view import GeneratorView
from .props_view import PropsView
from .markets_view import MarketsView
from .analyzer_view import AnalyzerView
from .team_slip_view import TeamSlipView


# Auto-refresh cadence for live odds polling. The Odds API rate-limits the
# free tier but 60s is well inside the quota for a single-sport pull and is
# the lowest interval Realsports.io-class scanners typically use.
AUTO_REFRESH_MS = 60_000


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=T.BG)
        self.title(f"{T.APP_NAME} — {T.APP_TAGLINE}")
        self.geometry("1440x900")
        self.minsize(1180, 720)

        ctk.set_appearance_mode("dark")

        self.state_ = AppState(config=load_config())
        self.state_.sport_key = self.state_.config.get("default_sport", "americanfootball_nfl")

        self._auto_refresh_job: str | None = None

        self._build_layout()
        self._wire()

        # Initial load + kick off the auto-refresh timer
        self.after(200, self.refresh_games)
        self._schedule_auto_refresh()

    # ---------------- Layout ----------------

    def _build_layout(self):
        # Root grid: sidebar | main | bet slip
        self.grid_columnconfigure(0, weight=0, minsize=210)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=380)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, fg_color=T.BG_ELEV_1, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsw")

        self.main = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_rowconfigure(0, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        self.slip = BetSlipPanel(self, self.state_)
        self.slip.grid(row=0, column=2, sticky="nse")

        self._build_sidebar()
        self._build_main_views()

    def _build_sidebar(self):
        # Brand
        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(22, 14))
        ctk.CTkLabel(brand, text=T.APP_NAME, font=T.FONT_HUGE, text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(brand, text=T.APP_TAGLINE, font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")

        # Navigation
        nav = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav.pack(fill="x", padx=12, pady=(10, 4))

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for key, label, icon in (
            ("games", "Board", "◼"),
            ("markets", "Markets", "▤"),
            ("analyzer", "Odds Analyzer", "◈"),
            ("team_slip", "Team Slip", "◆"),
            ("props", "Player Props", "◉"),
            ("generator", "Generator", "✦"),
            ("analysis", "Analysis", "◎"),
            ("settings", "Settings", "⚙"),
        ):
            b = ctk.CTkButton(
                nav, text=f"  {icon}   {label}",
                anchor="w", height=38,
                fg_color="transparent", hover_color=T.BG_ELEV_2,
                text_color=T.TEXT_MUTED, font=T.FONT_BOLD,
                corner_radius=8,
                command=lambda k=key: self.show(k),
            )
            b.pack(fill="x", pady=2)
            self.nav_buttons[key] = b

        # Divider
        ctk.CTkFrame(self.sidebar, height=1, fg_color=T.BORDER).pack(fill="x", pady=(10, 8), padx=16)

        # Sport picker
        ctk.CTkLabel(self.sidebar, text="SPORT", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w", padx=22)
        sports = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        sports.pack(fill="x", padx=12, pady=(4, 8))
        self.sport_buttons: dict[str, ctk.CTkButton] = {}
        for key, label in SPORT_LABELS.items():
            b = ctk.CTkButton(
                sports, text=f"  {label}",
                anchor="w", height=32,
                fg_color="transparent", hover_color=T.BG_ELEV_2,
                text_color=T.TEXT_MUTED, font=T.FONT_SMALL,
                corner_radius=6,
                command=lambda k=key: self.set_sport(k),
            )
            b.pack(fill="x", pady=1)
            self.sport_buttons[key] = b

        # Refresh button fixed at bottom
        bottom = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=16, pady=16)

        self.refresh_btn = ctk.CTkButton(
            bottom, text="↻  Refresh odds", height=38,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.BG,
            font=T.FONT_BOLD, corner_radius=8,
            command=self.refresh_games,
        )
        self.refresh_btn.pack(fill="x", pady=(0, 6))

        self.status_lbl = ctk.CTkLabel(bottom, text="", font=T.FONT_TINY, text_color=T.TEXT_MUTED)
        self.status_lbl.pack(anchor="w")

    def _build_main_views(self):
        self.views: dict[str, ctk.CTkFrame] = {}

        self.games_view = GamesView(self.main, self.state_, self._add_leg, self._view_analysis)
        self.views["games"] = self.games_view

        self.markets_view = MarketsView(self.main, self.state_, self._add_leg)
        self.views["markets"] = self.markets_view

        self.analyzer_view = AnalyzerView(self.main, self.state_, self._add_leg)
        self.views["analyzer"] = self.analyzer_view

        self.team_slip_view = TeamSlipView(self.main, self.state_, self._add_leg)
        self.views["team_slip"] = self.team_slip_view

        self.props_view = PropsView(self.main, self.state_, self._add_leg)
        self.views["props"] = self.props_view

        self.generator_view = GeneratorView(self.main, self.state_, self._add_leg)
        self.views["generator"] = self.generator_view

        self.analysis_view = AnalysisView(self.main, self.state_, self._add_leg)
        self.views["analysis"] = self.analysis_view

        self.settings_view = SettingsView(self.main, self.state_, self._on_settings_saved)
        self.views["settings"] = self.settings_view

        for v in self.views.values():
            v.grid(row=0, column=0, sticky="nsew")
            v.grid_remove()

        self.show("games")

    def _wire(self):
        self._highlight_sport(self.state_.sport_key)

    # ---------------- Navigation ----------------

    def show(self, key: str):
        for k, v in self.views.items():
            if k == key:
                v.grid()
            else:
                v.grid_remove()
        for k, b in self.nav_buttons.items():
            if k == key:
                b.configure(fg_color=T.BG_ELEV_2, text_color=T.ACCENT)
            else:
                b.configure(fg_color="transparent", text_color=T.TEXT_MUTED)

    def set_sport(self, key: str):
        if key == self.state_.sport_key:
            return
        self.state_.sport_key = key
        self.state_.config["default_sport"] = key
        save_config(self.state_.config)
        self._highlight_sport(key)
        self.state_.notify("sport")
        self.refresh_games()

    def _highlight_sport(self, key: str):
        for k, b in self.sport_buttons.items():
            if k == key:
                b.configure(fg_color=T.BG_ELEV_2, text_color=T.ACCENT)
            else:
                b.configure(fg_color="transparent", text_color=T.TEXT_MUTED)

    # ---------------- Actions ----------------

    def refresh_games(self):
        self.status_lbl.configure(text="Loading…", text_color=T.TEXT_MUTED)
        sport = self.state_.sport_key
        cfg = self.state_.config
        api_key = cfg.get("odds_api_key", "")
        use_demo = bool(cfg.get("use_demo_data_when_no_key", True))

        def work():
            err = ""
            games = []
            source = "demo"
            try:
                if api_key:
                    client = OddsAPI(api_key)
                    games = client.get_odds(
                        sport,
                        regions=cfg.get("default_region", "us"),
                        markets=cfg.get("default_markets", "h2h,spreads,totals"),
                    )
                    source = "live" if games else "demo"
                    if not games:
                        games = demo_games(sport)
                else:
                    if not use_demo:
                        err = "No API key configured. Open Settings to add one."
                    games = demo_games(sport)
                    source = "demo"
            except Exception as e:
                err = f"Failed to fetch odds: {e}"
                if use_demo:
                    games = demo_games(sport)
                source = "demo"

            self.after(0, lambda: self._on_games_loaded(games, err, source))

        threading.Thread(target=work, daemon=True).start()

    def _on_games_loaded(self, games, err: str, source: str = "demo"):
        self.state_.games = games
        self.state_.games_source = source
        self.state_.notify("games")
        if err:
            self.status_lbl.configure(text=err, text_color=T.NEGATIVE)
        else:
            label = SPORT_LABELS.get(self.state_.sport_key, self.state_.sport_key)
            tag = "LIVE" if source == "live" else "DEMO"
            self.status_lbl.configure(
                text=f"{tag} · {len(games)} {label} games.",
                text_color=T.POSITIVE if source == "live" else T.TEXT_MUTED,
            )

    def _add_leg(self, leg):
        self.state_.add_leg(leg)

    def _view_analysis(self, game_id: str):
        self.state_.selected_game_id = game_id
        self.show("analysis")
        self.analysis_view.show_game(game_id)

    def _on_settings_saved(self):
        self.status_lbl.configure(text="Settings saved.", text_color=T.POSITIVE)
        self.refresh_games()

    # ---------------- Auto-refresh ----------------

    def _schedule_auto_refresh(self):
        """Periodic background pull so Markets / Analyzer stay current.

        Only fires when an API key is configured — otherwise we'd just be
        reshuffling demo data. `self.after` returns a job handle which we
        hang onto so `_cancel_auto_refresh` can kill it on exit.
        """
        self._auto_refresh_job = self.after(AUTO_REFRESH_MS, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        cfg = self.state_.config
        if cfg.get("odds_api_key"):
            self.refresh_games()
        self._schedule_auto_refresh()

    def _cancel_auto_refresh(self):
        if self._auto_refresh_job is not None:
            try:
                self.after_cancel(self._auto_refresh_job)
            except Exception:
                pass
            self._auto_refresh_job = None

    def destroy(self):
        self._cancel_auto_refresh()
        super().destroy()
