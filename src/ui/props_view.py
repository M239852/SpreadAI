"""Player Props view: browse PrizePicks projections with modeled over/under
probabilities, filter, inspect a player's recent samples, and add to the slip.

The list is a native ttk.Treeview (virtualized) because a live NBA pull returns
thousands of projections.
"""
from __future__ import annotations
import threading
from typing import Callable
import customtkinter as ctk

from ..api.prizepicks_api import PrizePicksAPI, PlayerProp, PP_LEAGUE_IDS
from ..api.demo_props import demo_props
from ..analysis.props import analyze_prop, prop_to_leg, PropAnalysis
from ..analysis.probability import LegAnalysis
from . import theme as T
from .widgets import PageHeader, Toolbar, PrimaryButton, GhostButton, Sparkline, StatBlock, Tooltip, entry, option_menu, checkbox, make_tree
from .state import AppState
from .runtime import ui_call

ANALYZE_LIMIT = 200


class PropsView(ctk.CTkFrame):
    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self._props: list[PlayerProp] = []
        self._props_by_id: dict[str, PlayerProp] = {}
        self._analyses: dict[str, PropAnalysis] = {}
        self._use_demo = False
        self._analyze_network = True
        self._filter_query = ""
        self._filter_stat = "All"
        self._filter_team = "All"
        self._sort_col = "conf"
        self._sort_reverse = True
        self._selected_id: str | None = None

        self.header = PageHeader(self, "Player Props", "PrizePicks projections modeled against recent player history.")
        self.header.pack(fill="x")
        self.load_btn = PrimaryButton(self.header.actions, "↻  Load props", command=self._load, width=140, height=30)
        self.load_btn.pack(side="right", padx=(0, T.SP_2))
        self.header.set_source("none")

        self._build_filter_bar()
        self._build_table()
        self._build_detail_bar()
        state.subscribe(self._on_state_event)

    # ---------------------------------------------------------------- chrome

    def _build_filter_bar(self):
        bar = Toolbar(self)
        bar.pack(fill="x", padx=T.SP_4, pady=(0, T.SP_2))
        bar.label("Search")
        self.query_var = ctk.StringVar()
        entry(bar.inner, self.query_var, width=190, placeholder="Player or team…").pack(side="left", padx=(0, T.SP_4))
        self.query_var.trace_add("write", lambda *_: self._on_filter_change())
        bar.label("Stat")
        self.stat_menu = option_menu(bar.inner, ["All"], command=self._on_stat_change, width=170)
        self.stat_menu.pack(side="left", padx=(0, T.SP_4))
        self.stat_menu.set("All")
        bar.label("Team")
        self.team_menu = option_menu(bar.inner, ["All"], command=self._on_team_change, width=110)
        self.team_menu.pack(side="left", padx=(0, T.SP_4))
        self.team_menu.set("All")
        self.net_var = ctk.BooleanVar(value=True)
        cb = checkbox(bar.inner, "Use player history", self.net_var, command=self._on_network_toggle)
        cb.pack(side="right")
        Tooltip(cb, "Pull each player's recent game log from ESPN (slower, more accurate). Off = sport-default volatility around the line.")
        self.status_lbl = ctk.CTkLabel(self.header.actions, text="", font=T.FONT_TINY, text_color=T.TEXT_MUTED)
        self.status_lbl.pack(side="right", padx=(0, T.SP_3))

    def _build_table(self):
        self.tree, wrap = make_tree(self, {
            "player": ("Player", 160, "w"), "team": ("Team", 56, "w"), "stat": ("Stat", 130, "w"),
            "line": ("Line", 56, "e"), "proj": ("Proj", 56, "e"), "over": ("Over", 60, "e"),
            "under": ("Under", 60, "e"), "conf": ("Conf", 56, "e"), "dist": ("Dist", 64, "w"),
            "trend": ("Last 5", 120, "w"),
        }, style="Props.Treeview", stretch_col="trend")
        wrap.pack(fill="both", expand=True, padx=T.SP_4, pady=(0, T.SP_2))
        for c in ("player", "team", "stat", "line", "proj", "over", "under", "conf", "dist", "trend"):
            self.tree.heading(c, command=lambda col=c: self._on_header_click(col))
        self.tree.tag_configure("over", foreground=T.POSITIVE)
        self.tree.tag_configure("under", foreground=T.NEGATIVE)
        self.tree.tag_configure("mild", foreground=T.TEXT)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self._add_selected("Over"))

    def _build_detail_bar(self):
        bar = ctk.CTkFrame(self, fg_color=T.BG_ELEV_1, corner_radius=T.R_MD)
        bar.pack(fill="x", padx=T.SP_4, pady=(0, T.SP_4))
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=T.SP_3, pady=T.SP_2)

        # Right-anchored controls are packed first so they never get clipped
        # when the title text is long.
        self.under_btn = GhostButton(inner, "Add Under ↓", command=lambda: self._add_selected("Under"),
                                     width=104, height=32, state="disabled", hover_color=T.NEGATIVE_SOFT)
        self.under_btn.pack(side="right", padx=(T.SP_2, 0))
        self.over_btn = GhostButton(inner, "Add Over ↑", command=lambda: self._add_selected("Over"),
                                    width=104, height=32, state="disabled", hover_color=T.POSITIVE_SOFT)
        self.over_btn.pack(side="right")
        self.stat_under = StatBlock(inner, "Under", "—")
        self.stat_under.pack(side="right", padx=T.SP_3)
        self.stat_over = StatBlock(inner, "Over", "—")
        self.stat_over.pack(side="right", padx=T.SP_3)
        self.stat_proj = StatBlock(inner, "Projection", "—")
        self.stat_proj.pack(side="right", padx=T.SP_3)
        self.spark = Sparkline(inner, width=150, height=44, bg=T.BG_ELEV_1)
        self.spark.pack(side="right", padx=T.SP_3)

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        self.detail_title = ctk.CTkLabel(left, text="Select a row to see recent samples and add a side.",
                                         font=T.FONT_BOLD, text_color=T.TEXT_MUTED, anchor="w", justify="left", wraplength=300)
        self.detail_title.pack(anchor="w")
        self.detail_note = ctk.CTkLabel(left, text="", font=T.FONT_TINY, text_color=T.TEXT_DIM, anchor="w", justify="left", wraplength=300)
        self.detail_note.pack(anchor="w")

    # ---------------------------------------------------------------- behavior

    def _on_state_event(self, event: str):
        if event == "sport":
            self._props = []
            self._props_by_id.clear()
            self._analyses.clear()
            self._selected_id = None
            self._populate_tree()
            self.detail_title.configure(text="Sport switched. Click ‘Load props’ to pull the new slate.", text_color=T.TEXT_MUTED)
            self.over_btn.configure(state="disabled")
            self.under_btn.configure(state="disabled")

    def _on_filter_change(self):
        self._filter_query = (self.query_var.get() or "").strip().lower()
        self._populate_tree()
        self._analyze_visible_async()

    def _on_stat_change(self, val: str):
        self._filter_stat = val
        self._populate_tree()
        self._analyze_visible_async()

    def _on_team_change(self, val: str):
        self._filter_team = val
        self._populate_tree()
        self._analyze_visible_async()

    def _on_network_toggle(self):
        self._analyze_network = bool(self.net_var.get())

    def _on_header_click(self, col: str):
        mapping = {"player": "player", "team": "team", "stat": "stat", "line": "line", "proj": "proj",
                   "over": "conf", "under": "conf", "conf": "confidence", "dist": "dist", "trend": "trend"}
        new_sort = mapping.get(col, "conf")
        if self._sort_col == new_sort:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = new_sort
            self._sort_reverse = new_sort in ("conf", "confidence")
        self._populate_tree()

    def _on_tree_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            self._selected_id = None
            self.over_btn.configure(state="disabled")
            self.under_btn.configure(state="disabled")
            return
        pid = sel[0]
        self._selected_id = pid
        p = self._props_by_id.get(pid)
        a = self._analyses.get(pid)
        if not p:
            return
        self.detail_title.configure(text=f"{p.player_name}  ·  {p.stat_type} {p.line:g}  ·  {p.team or '?'} vs {p.opponent or '?'}",
                                    text_color=T.TEXT)
        if a:
            self.detail_note.configure(text=a.notes[0] if a.notes else "")
            self.spark.set(a.samples, p.line)
            self.stat_proj.set(f"{a.projection:.1f}", sub=f"σ {a.stdev:.1f} · {a.distribution}")
            self.stat_over.set(f"{a.over_prob*100:.0f}%", color=T.prob_color(a.over_prob), sub=f"{a.prob_low*100:.0f}–{a.prob_high*100:.0f}%")
            self.stat_under.set(f"{a.under_prob*100:.0f}%", color=T.prob_color(a.under_prob), sub=f"conf {a.confidence*100:.0f}%")
        else:
            self.detail_note.configure(text="Analyzing…")
            self.spark.set([], p.line)
            for s in (self.stat_proj, self.stat_over, self.stat_under):
                s.set("—", color=T.TEXT, sub="")
        self.over_btn.configure(state="normal")
        self.under_btn.configure(state="normal")

    def _add_selected(self, pick: str):
        if not self._selected_id:
            return
        p = self._props_by_id.get(self._selected_id)
        if not p:
            return
        a = self._analyses.get(p.id) or analyze_prop(p, skip_network=True)
        self.on_add_leg(prop_to_leg(p, a, pick))

    # ---------------------------------------------------------------- loading

    def _load(self):
        sport_key = self.state.sport_key
        if sport_key not in PP_LEAGUE_IDS:
            self.status_lbl.configure(text=f"PrizePicks doesn't cover {sport_key} in SpreadAI.", text_color=T.WARNING)
            return
        self.load_btn.configure(state="disabled", text="Loading…")
        self.status_lbl.configure(text="Fetching projections…", text_color=T.TEXT_MUTED)

        def work():
            props: list[PlayerProp] = []
            src = "PrizePicks"
            try:
                props = PrizePicksAPI().fetch_props(sport_key)
            except Exception:
                props = []
            if not props:
                props = demo_props(sport_key)
                src = "Demo"
            ui_call(self._on_props_loaded, props, src)

        threading.Thread(target=work, daemon=True).start()

    def _on_props_loaded(self, props: list[PlayerProp], src: str):
        self._props = props
        self._props_by_id = {p.id: p for p in props}
        self._analyses.clear()
        self._use_demo = (src == "Demo")
        self.state.props_source = "demo" if self._use_demo else "live"
        self.load_btn.configure(state="normal", text="↻  Load props")
        self.header.set_source("demo" if self._use_demo else "live", None if self._use_demo else "LIVE · PrizePicks")
        if self._use_demo:
            self.header.show_banner("PrizePicks feed unavailable — showing the built-in demo slate.")
        else:
            self.header.hide_banner()

        if not props:
            self.header.set_source("none")
            self.status_lbl.configure(text="No props available for this sport.", text_color=T.WARNING)
            self._populate_tree()
            return

        self.stat_menu.configure(values=["All"] + sorted({p.stat_type for p in props if p.stat_type}))
        self.team_menu.configure(values=["All"] + sorted({p.team for p in props if p.team}))
        self.status_lbl.configure(text=f"{len(props)} projections", text_color=T.POSITIVE)
        self._populate_tree()
        self._analyze_visible_async()

    def _analyze_visible_async(self):
        if self._props:
            threading.Thread(target=self._analyze_all, daemon=True).start()

    def _analyze_all(self):
        targets = [p for p in self._filtered() if p.id not in self._analyses][:ANALYZE_LIMIT]
        for i, p in enumerate(targets):
            try:
                analysis = analyze_prop(p, skip_network=not self._analyze_network)
            except Exception:
                analysis = analyze_prop(p, skip_network=True)
            self._analyses[p.id] = analysis
            if (i + 1) % 25 == 0:
                ui_call(self._populate_tree)
        ui_call(self._populate_tree)
        ui_call(self._on_tree_select)

    # ---------------------------------------------------------------- table

    def _filtered(self) -> list[PlayerProp]:
        q = self._filter_query
        out = self._props
        if q:
            out = [p for p in out if q in p.player_name.lower() or q in (p.team or "").lower()]
        if self._filter_stat != "All":
            out = [p for p in out if p.stat_type == self._filter_stat]
        if self._filter_team != "All":
            out = [p for p in out if p.team == self._filter_team]
        return out

    def _sort_key(self, p: PlayerProp):
        a = self._analyses.get(p.id)
        if self._sort_col == "conf":
            return (1, 0.0) if a is None else (0, -abs(a.over_prob - 0.5))
        if self._sort_col == "confidence":
            return (0, a.confidence if a else -1.0)
        if self._sort_col == "line":
            return (0, p.line)
        if self._sort_col == "proj":
            return (0, a.projection if a else -1.0)
        if self._sort_col == "dist":
            return (0, a.distribution if a else "")
        if self._sort_col == "trend":
            if a and a.samples:
                recent = a.samples[:5]
                return (0, sum(1 for v in recent if v > p.line) / max(1, len(recent)))
            return (1, 0.0)
        if self._sort_col == "player":
            return (0, p.player_name.lower())
        if self._sort_col == "team":
            return (0, (p.team or "").lower())
        if self._sort_col == "stat":
            return (0, p.stat_type.lower())
        return (0, 0)

    def _populate_tree(self):
        prior = self._selected_id
        self.tree.delete(*self.tree.get_children())
        rows = self._filtered()
        rows.sort(key=self._sort_key, reverse=self._sort_reverse)
        for p in rows:
            a = self._analyses.get(p.id)
            if a is not None:
                over_s, under_s = f"{a.over_prob*100:.0f}%", f"{a.under_prob*100:.0f}%"
                proj_s, conf_s, dist_s = f"{a.projection:.1f}", f"{a.confidence*100:.0f}%", a.distribution
                if a.samples:
                    hits = sum(1 for v in a.samples[:5] if v > p.line)
                    trend_s = f"{hits}/{min(5, len(a.samples))} over {p.line:g}"
                else:
                    trend_s = "no recent data"
                delta = a.over_prob - 0.5
                tag = "over" if delta > 0.08 else ("under" if delta < -0.08 else "mild")
            else:
                over_s = under_s = proj_s = conf_s = "—"
                dist_s, trend_s, tag = "", "", "dim"
            self.tree.insert("", "end", iid=p.id, values=(
                p.player_name, p.team or "", p.stat_type, f"{p.line:g}", proj_s, over_s, under_s, conf_s, dist_s, trend_s,
            ), tags=(tag,))
        if prior and self.tree.exists(prior):
            self.tree.selection_set(prior)
        total, shown = len(self._props), len(rows)
        if total and shown != total:
            self.header.set_subtitle(f"Showing {shown} of {total} projections · modeled vs. recent player history")
        elif total:
            self.header.set_subtitle(f"{total} projections · recency-weighted samples shrunk toward the line")
