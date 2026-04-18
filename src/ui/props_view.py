"""Player Props view: browse PrizePicks projections with modeled over/under
probabilities, filter, and add to the bet slip.

The list is a native ttk.Treeview — a C-backed virtualized table — because
CustomTkinter's scrollable card layout redraws every widget on each scroll
tick and doesn't stay responsive once you're past a few dozen rows. A live
NBA pull returns thousands of projections; Treeview handles that smoothly."""
from __future__ import annotations
import threading
from typing import Callable
import customtkinter as ctk
from tkinter import ttk

from ..api.prizepicks_api import PrizePicksAPI, PlayerProp, PP_LEAGUE_IDS, POWER_PAYOUTS
from ..api.demo_props import demo_props
from ..analysis.props import analyze_prop, prop_to_leg, PropAnalysis
from ..analysis.probability import LegAnalysis
from ..utils.formatters import format_pct
from . import theme as T
from .widgets import Pill
from .state import AppState

# Analyze at most this many visible props in the background — ESPN gamelog
# fetches are network-bound and we don't want to pound the API for 5k+ props
# the user is never going to look at. Treeview itself happily renders all rows.
ANALYZE_LIMIT = 200


class PropsView(ctk.CTkFrame):
    """Filterable, sortable table of player props with an action bar."""

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

        self._install_tree_style()
        self._build_header()
        self._build_filter_bar()
        self._build_table()
        self._build_action_bar()

        state.subscribe(self._on_state_event)

    # ---------------- Styling ----------------

    def _install_tree_style(self):
        """Dark-theme the ttk.Treeview to match the rest of the app."""
        style = ttk.Style()
        # The 'clam' theme is the most themable ttk base on Windows/mac/Linux.
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Props.Treeview",
            background=T.BG_ELEV_1,
            fieldbackground=T.BG_ELEV_1,
            foreground=T.TEXT,
            rowheight=26,
            borderwidth=0,
            font=T.FONT_SMALL,
        )
        style.configure(
            "Props.Treeview.Heading",
            background=T.BG_ELEV_2,
            foreground=T.TEXT_MUTED,
            relief="flat",
            font=T.FONT_BOLD,
        )
        style.map(
            "Props.Treeview",
            background=[("selected", T.ACCENT)],
            foreground=[("selected", T.BG)],
        )
        style.map(
            "Props.Treeview.Heading",
            background=[("active", T.BG_ELEV_3)],
        )

    # ---------------- Header + filter bar ----------------

    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 6))

        title_col = ctk.CTkFrame(head, fg_color="transparent")
        title_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(title_col, text="Player Props", font=T.FONT_TITLE, text_color=T.TEXT).pack(anchor="w")
        self.subtitle = ctk.CTkLabel(title_col, text="PrizePicks projections — modeled vs. player history",
                                     font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
        self.subtitle.pack(anchor="w", pady=(2, 0))

        self.load_btn = ctk.CTkButton(
            head, text="↻  Load props", height=36, width=160,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.BG,
            font=T.FONT_BOLD, corner_radius=8, command=self._load,
        )
        self.load_btn.pack(side="right")

        self.source_pill = Pill(head, "—", color=T.BG_ELEV_3, text_color=T.TEXT_MUTED)
        self.source_pill.pack(side="right", padx=10)

    def _build_filter_bar(self):
        bar = ctk.CTkFrame(self, fg_color=T.BG_ELEV_1, corner_radius=10)
        bar.pack(fill="x", padx=16, pady=4)
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(inner, text="Search", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        self.query_var = ctk.StringVar()
        ctk.CTkEntry(
            inner, textvariable=self.query_var, width=200, height=30,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT,
            placeholder_text="Player or team…",
        ).pack(side="left", padx=(6, 12))
        self.query_var.trace_add("write", lambda *_: self._on_filter_change())

        ctk.CTkLabel(inner, text="Stat", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        self.stat_menu = ctk.CTkOptionMenu(
            inner, values=["All"], width=180, height=30,
            fg_color=T.BG_ELEV_2, button_color=T.BG_ELEV_3, button_hover_color=T.BORDER,
            text_color=T.TEXT, dropdown_fg_color=T.BG_ELEV_2, command=self._on_stat_change,
        )
        self.stat_menu.pack(side="left", padx=(6, 12))
        self.stat_menu.set("All")

        ctk.CTkLabel(inner, text="Team", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        self.team_menu = ctk.CTkOptionMenu(
            inner, values=["All"], width=120, height=30,
            fg_color=T.BG_ELEV_2, button_color=T.BG_ELEV_3, button_hover_color=T.BORDER,
            text_color=T.TEXT, dropdown_fg_color=T.BG_ELEV_2, command=self._on_team_change,
        )
        self.team_menu.pack(side="left", padx=(6, 12))
        self.team_menu.set("All")

        self.net_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            inner, text="Model from player history (slower)",
            variable=self.net_var, font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            fg_color=T.ACCENT, border_color=T.BORDER, hover_color=T.ACCENT_HOVER,
            command=self._on_network_toggle,
        ).pack(side="right")

        self.status_lbl = ctk.CTkLabel(inner, text="", font=T.FONT_TINY, text_color=T.TEXT_MUTED)
        self.status_lbl.pack(side="right", padx=10)

    # ---------------- Table ----------------

    def _build_table(self):
        wrap = ctk.CTkFrame(self, fg_color=T.BG_ELEV_1, corner_radius=10)
        wrap.pack(fill="both", expand=True, padx=16, pady=(4, 8))

        cols = ("player", "team", "stat", "line", "over", "under", "avg", "trend")
        self.tree = ttk.Treeview(
            wrap,
            columns=cols,
            show="headings",
            style="Props.Treeview",
            selectmode="browse",
        )
        headings = {
            "player": ("Player", 220, "w"),
            "team":   ("Team",   70,  "w"),
            "stat":   ("Stat",   170, "w"),
            "line":   ("Line",   70,  "e"),
            "over":   ("Over %", 80,  "e"),
            "under":  ("Under %",80,  "e"),
            "avg":    ("Avg",    70,  "e"),
            "trend":  ("Last 5", 170, "w"),
        }
        for c, (label, w, anchor) in headings.items():
            self.tree.heading(c, text=label, command=lambda col=c: self._on_header_click(col))
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "trend"))

        # Per-row color for strong Over / Under leans. The tag is applied to a
        # row based on whichever side's probability is dominant.
        self.tree.tag_configure("over",  foreground=T.POSITIVE)
        self.tree.tag_configure("under", foreground=T.NEGATIVE)
        self.tree.tag_configure("mild",  foreground=T.TEXT)
        self.tree.tag_configure("dim",   foreground=T.TEXT_MUTED)

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self._add_selected("Over"))

    def _build_action_bar(self):
        bar = ctk.CTkFrame(self, fg_color=T.BG_ELEV_1, corner_radius=10)
        bar.pack(fill="x", padx=16, pady=(0, 14))
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        self.detail_label = ctk.CTkLabel(
            inner, text="Select a row to see detail and add to slip.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w",
        )
        self.detail_label.pack(side="left", fill="x", expand=True)

        self.over_btn = ctk.CTkButton(
            inner, text="Add Over ↑", width=110, height=32,
            fg_color=T.BG_ELEV_3, hover_color=T.POSITIVE, text_color=T.TEXT,
            font=T.FONT_BOLD, corner_radius=6, state="disabled",
            command=lambda: self._add_selected("Over"),
        )
        self.over_btn.pack(side="right", padx=(6, 0))
        self.under_btn = ctk.CTkButton(
            inner, text="Add Under ↓", width=110, height=32,
            fg_color=T.BG_ELEV_3, hover_color=T.NEGATIVE, text_color=T.TEXT,
            font=T.FONT_BOLD, corner_radius=6, state="disabled",
            command=lambda: self._add_selected("Under"),
        )
        self.under_btn.pack(side="right", padx=6)

    # ---------------- Behavior ----------------

    def _on_state_event(self, event: str):
        if event == "sport":
            self._props = []
            self._props_by_id.clear()
            self._analyses.clear()
            self._selected_id = None
            self._populate_tree()
            self.detail_label.configure(text="Sport switched. Click ‘Load props’ to pull the new slate.")
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
        mapping = {
            "player": "player", "team": "team", "stat": "stat", "line": "line",
            "over": "conf", "under": "conf", "avg": "avg", "trend": "trend",
        }
        new_sort = mapping.get(col, "conf")
        if self._sort_col == new_sort:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = new_sort
            self._sort_reverse = (new_sort == "conf")
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
        if a and a.samples:
            note = (f"{p.player_name} · {p.stat_type} line {p.line}   "
                    f"Over {a.over_prob*100:.1f}%  ·  Under {a.under_prob*100:.1f}%  ·  "
                    f"last-{len(a.samples)} avg {a.season_avg:.1f}")
        else:
            note = f"{p.player_name} · {p.stat_type} line {p.line}   (no recent history — using sport default)"
        self.detail_label.configure(text=note, text_color=T.TEXT)
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

    # ---------------- Loading ----------------

    def _load(self):
        sport_key = self.state.sport_key
        if sport_key not in PP_LEAGUE_IDS:
            self.status_lbl.configure(
                text=f"PrizePicks doesn't cover {sport_key} in SpreadAI.",
                text_color=T.WARNING,
            )
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
            self.after(0, lambda: self._on_props_loaded(props, src))

        threading.Thread(target=work, daemon=True).start()

    def _on_props_loaded(self, props: list[PlayerProp], src: str):
        self._props = props
        self._props_by_id = {p.id: p for p in props}
        self._analyses.clear()
        self._use_demo = (src == "Demo")
        self.state.props_source = "demo" if self._use_demo else "live"
        self.load_btn.configure(state="normal", text="↻  Load props")

        if self._use_demo:
            self.source_pill.configure(text="DEMO", text_color=T.TEXT_MUTED)
        else:
            self.source_pill.configure(text="LIVE · PrizePicks", text_color=T.POSITIVE)

        if not props:
            self.source_pill.configure(text="—", text_color=T.TEXT_MUTED)
            self.status_lbl.configure(text="No props available for this sport.", text_color=T.WARNING)
            self._populate_tree()
            return

        stats = sorted({p.stat_type for p in props if p.stat_type})
        teams = sorted({p.team for p in props if p.team})
        self.stat_menu.configure(values=["All"] + stats)
        self.team_menu.configure(values=["All"] + teams)

        src_note = "source: PrizePicks" if src == "PrizePicks" else "source: built-in demo slate"
        self.status_lbl.configure(text=f"{len(props)} projections · {src_note}", text_color=T.POSITIVE)
        self.subtitle.configure(text=f"{len(props)} projections · modeled vs. recent player history")

        self._populate_tree()
        self._analyze_visible_async()

    def _analyze_visible_async(self):
        if self._props:
            threading.Thread(target=self._analyze_all, daemon=True).start()

    def _analyze_all(self):
        # Analyze the current filter's top N — good enough to populate the
        # visible viewport without drowning in ESPN gamelog fetches.
        targets = [p for p in self._filtered() if p.id not in self._analyses][:ANALYZE_LIMIT]
        for i, p in enumerate(targets):
            try:
                analysis = analyze_prop(p, skip_network=not self._analyze_network)
            except Exception:
                analysis = analyze_prop(p, skip_network=True)
            self._analyses[p.id] = analysis
            if (i + 1) % 25 == 0:
                self.after(0, self._populate_tree)
        self.after(0, self._populate_tree)

    # ---------------- Filtering + sort + render ----------------

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
            # Analyzed rows first, ranked by distance from 50/50.
            if a is None:
                return (1, 0.0)
            return (0, -abs(a.over_prob - 0.5))
        if self._sort_col == "line":
            return (0, p.line)
        if self._sort_col == "avg":
            return (0, a.season_avg if a else -1.0)
        if self._sort_col == "trend":
            if a and a.samples:
                recent = a.samples[:5]
                hits = sum(1 for v in recent if v > p.line)
                return (0, hits / max(1, len(recent)))
            return (1, 0.0)
        if self._sort_col == "player":
            return (0, p.player_name.lower())
        if self._sort_col == "team":
            return (0, (p.team or "").lower())
        if self._sort_col == "stat":
            return (0, p.stat_type.lower())
        return (0, 0)

    def _populate_tree(self):
        # Remember selection to reapply after repopulating.
        prior = self._selected_id

        for iid in self.tree.get_children():
            self.tree.delete(iid)

        rows = self._filtered()
        rows.sort(key=self._sort_key, reverse=self._sort_reverse)

        for p in rows:
            a = self._analyses.get(p.id)
            if a is not None:
                over_s = f"{a.over_prob * 100:.0f}%"
                under_s = f"{a.under_prob * 100:.0f}%"
                avg_s = f"{a.season_avg:.1f}"
                if a.samples:
                    hits = sum(1 for v in a.samples[:5] if v > p.line)
                    trend_s = f"{hits}/{min(5, len(a.samples))} over {p.line}"
                else:
                    trend_s = "no recent data"
                delta = a.over_prob - 0.5
                if delta > 0.08:
                    tag = "over"
                elif delta < -0.08:
                    tag = "under"
                else:
                    tag = "mild"
            else:
                over_s = under_s = avg_s = "—"
                trend_s = ""
                tag = "dim"

            self.tree.insert(
                "", "end", iid=p.id,
                values=(
                    p.player_name,
                    p.team or "",
                    p.stat_type,
                    f"{p.line:g}",
                    over_s,
                    under_s,
                    avg_s,
                    trend_s,
                ),
                tags=(tag,),
            )

        if prior and self.tree.exists(prior):
            self.tree.selection_set(prior)

        total = len(self._props)
        shown = len(rows)
        if total and shown != total:
            self.subtitle.configure(text=f"Showing {shown} of {total} projections")
        elif total:
            self.subtitle.configure(text=f"{total} projections · modeled vs. recent player history")
