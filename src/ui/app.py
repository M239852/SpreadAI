"""Application shell: sidebar navigation, top command bar, view stack, bet slip drawer."""
from __future__ import annotations
import logging
import time
from datetime import datetime
from typing import Callable
import customtkinter as ctk

from ..api.odds_api import OddsAPI, SPORT_LABELS
from ..api.demo_data import demo_games
from ..analysis import model as M
from ..utils.storage import load_config, save_config
from . import theme as T
from .widgets import Pill, Segmented, IconButton, PrimaryButton, GhostButton, Tooltip, install_treeview_styles
from .state import AppState
from .runtime import start_pump, run_in_thread
from .games_view import GamesView
from .analysis_view import AnalysisView
from .betslip_view import BetSlipPanel
from .settings_view import SettingsView
from .generator_view import GeneratorView
from .props_view import PropsView
from .markets_view import MarketsView
from .analyzer_view import AnalyzerView
from .team_slip_view import TeamSlipView
from .prop_generator_view import PropGeneratorView


log = logging.getLogger("spreadai.app")

# Auto-refresh cadence for live odds polling. The Odds API rate-limits the
# free tier but 60s is well inside the quota for a single-sport pull. The
# client caches for 120 s and the app only re-renders when prices changed,
# so a tick that finds nothing new costs a cache read and a status update.
AUTO_REFRESH_MS = 60_000

# Navigation, grouped. (key, label, icon, shortcut digit)
NAV_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Trade", [
        ("games",    "Board",         "▦"),
        ("markets",  "Markets",       "▤"),
        ("analyzer", "Odds Analyzer", "◈"),
    ]),
    ("Build", [
        ("team_slip",      "Team Slip",      "◆"),
        ("props",          "Player Props",   "◉"),
        ("prop_generator", "Prop Generator", "✧"),
        ("generator",      "Parlay Generator", "✦"),
    ]),
    ("Research", [
        ("analysis", "Game Analysis", "◎"),
    ]),
]


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=T.BG)
        self.title(f"{T.APP_NAME} — {T.APP_TAGLINE}")
        self.geometry("1480x920")
        self.minsize(1180, 720)
        ctk.set_appearance_mode("dark")
        install_treeview_styles()

        self.state_ = AppState(config=load_config())
        self.state_.sport_key = self.state_.config.get("default_sport", "americanfootball_nfl")
        M.configure(M.settings_from_config(self.state_.config))

        self._auto_refresh_job: str | None = None
        self._sidebar_collapsed = False
        self._slip_visible = True
        self._current_view = "games"
        self._last_refresh: datetime | None = None
        self._refreshing = False
        self._games_fingerprint: tuple | None = None

        self._build_layout()
        self._bind_shortcuts()
        self.state_.subscribe(self._on_state_event)

        # All worker-thread results come back through one queue drained here.
        start_pump(self)
        self.after(200, self.refresh_games)
        self._schedule_auto_refresh()

    def report_callback_exception(self, exc, val, tb):
        """Tk calls this for exceptions inside event callbacks; log instead of printing."""
        log.error("Tk callback failed", exc_info=(exc, val, tb))

    # ================================================================ layout

    def _build_layout(self):
        # Root grid: sidebar | (topbar over main) | slip
        self.grid_columnconfigure(0, weight=0, minsize=T.SIDEBAR_W)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self.sidebar = ctk.CTkFrame(self, fg_color=T.BG_ELEV_1, corner_radius=0, width=T.SIDEBAR_W)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        self.sidebar.grid_propagate(False)

        self.topbar = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0, height=T.TOPBAR_H)
        self.topbar.grid(row=0, column=1, columnspan=2, sticky="new")
        self.topbar.grid_propagate(False)

        self.main = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        self.main.grid(row=1, column=1, sticky="nsew")
        self.main.grid_rowconfigure(0, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        self.slip = BetSlipPanel(self, self.state_)
        self.slip.grid(row=1, column=2, sticky="nse")

        self._build_sidebar()
        self._build_topbar()
        self._build_main_views()

    # ---------------------------------------------------------------- sidebar

    def _build_sidebar(self):
        sb = self.sidebar

        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.pack(fill="x", padx=T.SP_4, pady=(T.SP_5, T.SP_3))
        self.brand_lbl = ctk.CTkLabel(brand, text=T.APP_NAME, font=T.FONT_HUGE, text_color=T.TEXT, anchor="w")
        self.brand_lbl.pack(side="left")
        self.collapse_btn = IconButton(brand, "‹", command=self.toggle_sidebar, width=28)
        self.collapse_btn.pack(side="right")
        Tooltip(self.collapse_btn, "Collapse sidebar")
        self.brand_sub = ctk.CTkLabel(sb, text=f"{T.APP_TAGLINE}  ·  model v{M.MODEL_VERSION}",
                                      font=T.FONT_TINY, text_color=T.TEXT_MUTED, anchor="w")
        self.brand_sub.pack(fill="x", padx=T.SP_4)

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_meta: dict[str, tuple[str, str]] = {}
        self._group_labels: list[ctk.CTkLabel] = []
        nav = ctk.CTkFrame(sb, fg_color="transparent")
        nav.pack(fill="x", padx=T.SP_3, pady=(T.SP_4, 0))
        shortcut = 1
        for group, items in NAV_GROUPS:
            lbl = ctk.CTkLabel(nav, text=group.upper(), font=T.FONT_LABEL, text_color=T.TEXT_DIM, anchor="w")
            lbl.pack(fill="x", padx=T.SP_2, pady=(T.SP_3, 2))
            self._group_labels.append(lbl)
            for key, label, icon in items:
                b = ctk.CTkButton(
                    nav, text=f"  {icon}   {label}", anchor="w", height=36,
                    fg_color="transparent", hover_color=T.BG_ELEV_2,
                    text_color=T.TEXT_MUTED, font=T.FONT_BOLD, corner_radius=T.R_SM,
                    command=lambda k=key: self.show(k),
                )
                b.pack(fill="x", pady=1)
                Tooltip(b, f"{label}   (Ctrl+{shortcut})" if shortcut <= 9 else label)
                self.nav_buttons[key] = b
                self._nav_meta[key] = (label, icon)
                shortcut += 1

        # Footer: settings + bankroll summary
        footer = ctk.CTkFrame(sb, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=T.SP_3, pady=T.SP_3)
        self.bankroll_card = ctk.CTkFrame(footer, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        self.bankroll_card.pack(fill="x", pady=(0, T.SP_2))
        inner = ctk.CTkFrame(self.bankroll_card, fg_color="transparent")
        inner.pack(fill="x", padx=T.SP_3, pady=T.SP_2)
        ctk.CTkLabel(inner, text="BANKROLL", font=T.FONT_LABEL, text_color=T.TEXT_MUTED, anchor="w").pack(anchor="w")
        self.bankroll_lbl = ctk.CTkLabel(inner, text="—", font=T.FONT_HEAD, text_color=T.TEXT, anchor="w")
        self.bankroll_lbl.pack(anchor="w")
        self.kelly_lbl = ctk.CTkLabel(inner, text="", font=T.FONT_TINY, text_color=T.TEXT_DIM, anchor="w")
        self.kelly_lbl.pack(anchor="w")
        self._refresh_bankroll_card()

        b = ctk.CTkButton(
            footer, text="  ⚙   Settings", anchor="w", height=36,
            fg_color="transparent", hover_color=T.BG_ELEV_2,
            text_color=T.TEXT_MUTED, font=T.FONT_BOLD, corner_radius=T.R_SM,
            command=lambda: self.show("settings"),
        )
        b.pack(fill="x")
        self.nav_buttons["settings"] = b
        self._nav_meta["settings"] = ("Settings", "⚙")

    def toggle_sidebar(self):
        self._sidebar_collapsed = not self._sidebar_collapsed
        collapsed = self._sidebar_collapsed
        width = T.SIDEBAR_W_COLLAPSED if collapsed else T.SIDEBAR_W
        self.sidebar.configure(width=width)
        self.grid_columnconfigure(0, minsize=width)
        self.brand_lbl.configure(text="S" if collapsed else T.APP_NAME)
        self.collapse_btn.configure(text="›" if collapsed else "‹")
        if collapsed:
            self.brand_sub.pack_forget()
            self.bankroll_card.pack_forget()
            for lbl in self._group_labels:
                lbl.pack_forget()
        else:
            self.brand_sub.pack(fill="x", padx=T.SP_4, after=self.brand_lbl.master)
            self.bankroll_card.pack(fill="x", pady=(0, T.SP_2), before=self.nav_buttons["settings"])
            # Group labels need re-packing in order; simplest is to re-pack all nav children.
            self._repack_nav()
        for key, b in self.nav_buttons.items():
            label, icon = self._nav_meta[key]
            b.configure(text=(f" {icon}" if collapsed else f"  {icon}   {label}"),
                        anchor=("center" if collapsed else "w"))

    def _repack_nav(self):
        nav = next(iter(self.nav_buttons.values())).master
        for child in nav.winfo_children():
            child.pack_forget()
        for group, items in NAV_GROUPS:
            lbl = next((l for l in self._group_labels if l.cget("text") == group.upper()), None)
            if lbl is not None:
                lbl.pack(fill="x", padx=T.SP_2, pady=(T.SP_3, 2))
            for key, _label, _icon in items:
                self.nav_buttons[key].pack(fill="x", pady=1)

    def _refresh_bankroll_card(self):
        cfg = self.state_.config
        try:
            bank = float(cfg.get("bankroll") or 0)
        except (TypeError, ValueError):
            bank = 0.0
        try:
            kf = float(cfg.get("kelly_fraction") or 0.25)
        except (TypeError, ValueError):
            kf = 0.25
        self.bankroll_lbl.configure(text=f"${bank:,.0f}")
        self.kelly_lbl.configure(text=f"{kf:g}× Kelly · {'conservative' if M.current_settings().kelly_conservative else 'point estimate'}")

    # ---------------------------------------------------------------- topbar

    def _build_topbar(self):
        bar = ctk.CTkFrame(self.topbar, fg_color="transparent")
        bar.pack(fill="both", expand=True, padx=T.PAGE_PAD_X, pady=(T.SP_3, T.SP_2))

        self.sport_seg = Segmented(
            bar, [(k, v) for k, v in SPORT_LABELS.items()],
            value=self.state_.sport_key, command=self.set_sport, height=28, font=T.FONT_SMALL,
        )
        self.sport_seg.pack(side="left")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right")

        self.slip_btn = GhostButton(right, "Slip", command=self.toggle_slip, width=96, height=30)
        self.slip_btn.pack(side="right", padx=(T.SP_2, 0))
        Tooltip(self.slip_btn, "Show / hide the bet slip   (Ctrl+B)")

        self.refresh_btn = PrimaryButton(right, "↻  Refresh", command=self.refresh_games, height=30, width=110)
        self.refresh_btn.pack(side="right", padx=(T.SP_2, 0))
        Tooltip(self.refresh_btn, "Pull fresh odds   (Ctrl+R)")

        self.status_lbl = ctk.CTkLabel(right, text="", font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
        self.status_lbl.pack(side="right", padx=(T.SP_3, T.SP_2))
        self.source_pill = Pill(right, "DEMO", variant="neutral")
        self.source_pill.pack(side="right")

        ctk.CTkFrame(self.topbar, height=1, fg_color=T.BORDER).pack(side="bottom", fill="x")

    def _update_slip_button(self):
        n = len(self.state_.bet_slip)
        text = f"Slip · {n}" if n else "Slip"
        self.slip_btn.configure(
            text=text,
            fg_color=(T.ACCENT_SOFT if self._slip_visible else T.BG_ELEV_3),
            text_color=(T.ACCENT if self._slip_visible else T.TEXT),
        )

    def toggle_slip(self):
        self._slip_visible = not self._slip_visible
        if self._slip_visible:
            self.slip.grid()
        else:
            self.slip.grid_remove()
        self._update_slip_button()

    # ---------------------------------------------------------------- views

    def _build_main_views(self):
        """Register view factories; each screen is constructed on first use.

        Building all nine views up front cost roughly two seconds of widget
        creation before the window could paint. Only the Board is needed at
        startup, so the rest are constructed the first time they are shown and
        cached from then on.
        """
        self._view_factories: dict[str, Callable[[], ctk.CTkFrame]] = {
            "games":          lambda: GamesView(self.main, self.state_, self._add_leg, self._view_analysis),
            "markets":        lambda: MarketsView(self.main, self.state_, self._add_leg),
            "analyzer":       lambda: AnalyzerView(self.main, self.state_, self._add_leg),
            "team_slip":      lambda: TeamSlipView(self.main, self.state_, self._add_leg),
            "props":          lambda: PropsView(self.main, self.state_, self._add_leg),
            "prop_generator": lambda: PropGeneratorView(self.main, self.state_, self._add_leg),
            "generator":      lambda: GeneratorView(self.main, self.state_, self._add_leg),
            "analysis":       lambda: AnalysisView(self.main, self.state_, self._add_leg),
            "settings":       lambda: SettingsView(self.main, self.state_, self._on_settings_saved),
        }
        self.views: dict[str, ctk.CTkFrame] = {}
        self.show("games")
        self._update_slip_button()

    def view(self, key: str):
        """Return a view, constructing it the first time it is asked for."""
        v = self.views.get(key)
        if v is None:
            factory = self._view_factories.get(key)
            if factory is None:
                return None
            t0 = time.perf_counter()
            v = factory()
            v.grid(row=0, column=0, sticky="nsew")
            v.grid_remove()
            self.views[key] = v
            log.debug("built view %r in %.0f ms", key, (time.perf_counter() - t0) * 1000)
        return v

    # Views the rest of the app reaches for by name; each builds on demand.
    @property
    def games_view(self):
        return self.view("games")

    @property
    def analysis_view(self):
        return self.view("analysis")

    @property
    def props_view(self):
        return self.view("props")

    @property
    def generator_view(self):
        return self.view("generator")

    @property
    def prop_generator_view(self):
        return self.view("prop_generator")

    @property
    def team_slip_view(self):
        return self.view("team_slip")

    @property
    def markets_view(self):
        return self.view("markets")

    @property
    def analyzer_view(self):
        return self.view("analyzer")

    @property
    def settings_view(self):
        return self.view("settings")

    def show(self, key: str):
        if key not in self._view_factories:
            return
        target = self.view(key)
        if target is None:
            return
        previous = self.views.get(self._current_view)
        self._current_view = key
        for k, v in self.views.items():
            if k == key:
                v.grid()
            else:
                v.grid_remove()
        # Hidden views skip re-rendering; the one being shown catches up.
        if previous is not None and previous is not target and hasattr(previous, "on_hidden"):
            previous.on_hidden()
        if hasattr(target, "on_shown"):
            target.on_shown()
        for k, b in self.nav_buttons.items():
            if k == key:
                b.configure(fg_color=T.ACCENT_SOFT, text_color=T.ACCENT)
            else:
                b.configure(fg_color="transparent", text_color=T.TEXT_MUTED)

    def _bind_shortcuts(self):
        self.bind("<Control-r>", lambda _e: self.refresh_games())
        self.bind("<Control-b>", lambda _e: self.toggle_slip())
        self.bind("<Control-bracketleft>", lambda _e: self.toggle_sidebar())
        keys = [k for _g, items in NAV_GROUPS for k, _l, _i in items]
        for i, key in enumerate(keys[:9], start=1):
            self.bind(f"<Control-Key-{i}>", lambda _e, k=key: self.show(k))
        self.bind("<Control-comma>", lambda _e: self.show("settings"))

    # ---------------------------------------------------------------- state

    def _on_state_event(self, event: str):
        if event == "betslip":
            self._update_slip_button()

    def set_sport(self, key: str):
        if key == self.state_.sport_key:
            return
        self.state_.sport_key = key
        self.state_.config["default_sport"] = key
        save_config(self.state_.config)
        self.sport_seg.set(key)
        self.state_.notify("sport")
        self._refreshing = False      # a stale in-flight pull is dropped on arrival
        self.refresh_games()

    # ---------------------------------------------------------------- actions

    def refresh_games(self, quiet: bool = False):
        """Pull odds on a worker thread and hand the result to the UI thread.

        `quiet` is used by the auto-refresh tick: no "Loading…" flash, and if
        the prices did not change nothing is re-rendered.
        """
        if self._refreshing:
            log.debug("refresh already in flight — ignored")
            return
        self._refreshing = True
        if not quiet:
            self.status_lbl.configure(text="Loading…", text_color=T.TEXT_MUTED)
        self.refresh_btn.configure(state="disabled")
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
                log.warning("odds fetch failed: %s", e)
                err = f"Failed to fetch odds: {e}"
                if use_demo:
                    games = demo_games(sport)
                source = "demo"
            return games, err, source, sport, quiet

        run_in_thread(work, on_done=lambda r: self._on_games_loaded(*r),
                      on_error=self._on_refresh_error, name="odds-refresh")

    def _on_refresh_error(self, exc: BaseException):
        self._refreshing = False
        self.refresh_btn.configure(state="normal")
        self.status_lbl.configure(text=f"Refresh failed: {exc}", text_color=T.NEGATIVE)

    @staticmethod
    def _fingerprint(games) -> tuple:
        """Cheap identity of a slate: every (game, book, market, outcome, price, point)."""
        return tuple(sorted(
            (g.id, bk.key, m.key, o.name, o.price, o.point)
            for g in games for bk in g.bookmakers for m in bk.markets for o in m.outcomes
        ))

    def _on_games_loaded(self, games, err: str, source: str = "demo", sport: str | None = None, quiet: bool = False):
        self._refreshing = False
        self.refresh_btn.configure(state="normal")
        if sport is not None and sport != self.state_.sport_key:
            log.info("dropping stale refresh for %s (now on %s)", sport, self.state_.sport_key)
            return

        fingerprint = self._fingerprint(games)
        changed = fingerprint != self._games_fingerprint or source != self.state_.games_source
        self._games_fingerprint = fingerprint
        self.state_.games = games
        self.state_.games_source = source
        self._last_refresh = datetime.now()
        if changed or not quiet:
            self.state_.notify("games")
        else:
            log.debug("auto-refresh: prices unchanged, skipped re-render")

        label = SPORT_LABELS.get(self.state_.sport_key, self.state_.sport_key)
        stamp = self._last_refresh.strftime("%H:%M")
        if err:
            self.status_lbl.configure(text=err, text_color=T.NEGATIVE)
        else:
            self.status_lbl.configure(
                text=f"{len(games)} {label} games  ·  updated {stamp}",
                text_color=T.TEXT_MUTED,
            )
        self.source_pill.set("LIVE" if source == "live" else "DEMO",
                             variant=("positive" if source == "live" else "neutral"))

    def _add_leg(self, leg):
        self.state_.add_leg(leg)
        if not self._slip_visible:
            self.toggle_slip()

    def _view_analysis(self, game_id: str):
        self.state_.selected_game_id = game_id
        self.show("analysis")
        self.analysis_view.show_game(game_id)

    def _on_settings_saved(self):
        M.configure(M.settings_from_config(self.state_.config))
        self._refresh_bankroll_card()
        self.state_.notify("settings")
        self.status_lbl.configure(text="Settings saved.", text_color=T.POSITIVE)
        self.refresh_games()

    # ---------------------------------------------------------------- auto-refresh

    def _schedule_auto_refresh(self):
        self._auto_refresh_job = self.after(AUTO_REFRESH_MS, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        if self.state_.config.get("odds_api_key") and not self._refreshing:
            self.refresh_games(quiet=True)
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
