"""The element table an agent reads, and the renderer that keeps it cheap.

Design goal: one line per actionable element, no screenshots, no raw DOM. Every
line is enough to pick a ref and an operation, and nothing more. Two economies
matter more than any others when an LLM is the policy:

  * `ref` values are stable across observations, so a plan written three steps
    ago still refers to the same control;
  * unchanged state is not re-sent — a delta observation is usually a handful
    of lines instead of a full table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

ROLE_CODE = {
    "button": "btn", "link": "lnk", "textbox": "inp", "searchbox": "srch",
    "combobox": "cmb", "checkbox": "chk", "radio": "rad", "switch": "sw",
    "tab": "tab", "menuitem": "mi", "menuitemcheckbox": "mic", "menuitemradio": "mir",
    "option": "opt", "treeitem": "tree", "gridcell": "cell", "spinbutton": "spin",
    "listbox": "list", "file": "file",
}

EDITABLE_ROLES = {"textbox", "searchbox", "spinbutton", "combobox"}


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


@dataclass
class Element:
    ref: str
    role: str
    name: str
    value: str = ""
    editable: bool = False
    occluded: bool = False
    checked: bool | None = None
    current: str | None = None
    expanded: str | None = None
    options: list[dict] = field(default_factory=list)
    opts_total: int = 0
    secret: bool = False
    context: str = ""
    accept: str | None = None
    multiple: bool = False
    label: str = ""
    in_viewport: bool = True
    # True when the element is a menu trigger -- it names or tracks a popup, so
    # whatever it opens is only reachable after pointing at it. The observer
    # decides this from `aria-haspopup`/`aria-expanded`; see js/observer.js.
    hoverable: bool = False

    @property
    def code(self) -> str:
        return ROLE_CODE.get(self.role, self.role[:4])

    @classmethod
    def from_raw(cls, raw: dict, *, secret: bool = False) -> "Element":
        return cls(
            ref=raw["ref"],
            role=raw.get("role") or "generic",
            name=_short(raw.get("name") or "", 160),
            value=_short(raw.get("value") or "", 160),
            editable=bool(raw.get("editable")),
            occluded=bool(raw.get("occluded")),
            checked=raw.get("checked"),
            current=_short(raw.get("current") or "", 120) if raw.get("current") else None,
            expanded=raw.get("expanded"),
            options=raw.get("options") or [],
            opts_total=raw.get("opts_total") or 0,
            secret=bool(raw.get("secret")) or secret,
            context=_short(raw.get("context") or "", 100),
            accept=raw.get("accept"),
            multiple=bool(raw.get("multiple")),
            label=_short(raw.get("label") or "", 160),
            hoverable=bool(raw.get("hoverable")),
            # The observer reports this as `inViewport`. Dropping it left `in_viewport`
            # permanently True, so the `»` flag never rendered even though the header
            # advertises it and counts the same elements in `offscreen`.
            in_viewport=bool(raw.get("inViewport", True)),
        )

    # ---------------------------------------------------------------- helpers

    def signature(self) -> tuple:
        """Everything that, when changed, is worth telling the agent about."""
        return (
            self.role, self.name, self.value, self.checked, self.current,
            self.expanded, self.occluded, self.editable,
        )

    def target_kinds(self) -> list[str]:
        """Operations this element can perform, in the canonical operation names.

        These strings travel to the decision model as its `criteria` keys and
        come back as the chosen operation, so they must match the vocabulary
        the caller dispatches on -- see `policy.OPERATION_TO_ACT`.
        """
        kinds = []
        if self.editable:
            kinds.append("TYPE_TEXT")
        if self.role in {"checkbox", "radio", "switch"}:
            kinds.append("TOGGLE")
        elif self.role == "file":
            kinds.append("UPLOAD")
        elif self.role in {"combobox", "listbox"} and self.options:
            kinds.append("SELECT")
            kinds.append("CLICK")
        else:
            kinds.append("CLICK")
        if self.hoverable:
            # Not an alternative to CLICK: whatever this trigger opens is absent
            # from the table until the pointer rests on it, so the trigger has to
            # be hovered before anything behind it can even be named.
            kinds.append("HOVER")
        return kinds

    def render(self, *, detail: bool = False, mask: bool = True) -> str:
        flags = ""
        if self.editable:
            flags += "*"
        if self.occluded:
            flags += "\u2298"
        elif not self.in_viewport:
            flags += "\u00bb"          # off-screen: act will scroll it into view
        if self.hoverable and self.expanded != "true":
            flags += "\u22ee"          # menu trigger: hover it, then choose
        if self.expanded == "true":
            flags += "\u25be"
        if self.checked is True:
            flags += "\u2713"
        elif self.checked is False:
            flags += "\u00b7"

        head = f"{self.ref:<5}{self.code}{flags:<3}"
        body = self.name or self.label or f"<{self.role}>"

        if self.role == "file":
            body = f"{body} accept={self.accept or '*'}" + (" [multiple]" if self.multiple else "")
        elif self.role in {"combobox", "listbox"} and self.options:
            shown = self.options[: (20 if detail else 8)]
            rendered = []
            for option in shown:
                label = _short(option.get("label") or "", 24)
                value = option.get("value")
                rendered.append(label if value in (None, "", label) else f"{label}={_short(str(value), 18)}")
            more = "" if self.opts_total <= len(shown) else f" +{self.opts_total - len(shown)}"
            body = f"{body} \u25b8 {self.current or '(none)'} opts{{{ ' | '.join(rendered) }{more}}}"
        elif self.editable:
            value = self.value or ""
            if self.secret and mask:
                value = "\u00abhidden\u00bb" if value else ""
            body = f"{body} \u25b8 \"{value}\""
        elif self.value:
            body = f"{body} \u25b8 \"{self.value}\""

        if self.context and not detail:
            body += f"  @{self.context}"
        return f"{head} {body}"


@dataclass
class Observation:
    url: str
    title: str
    text: str
    elements: list[Element]
    digest: str
    text_digest: str
    page_key: str
    scroll: dict
    reachable: int
    omitted: int
    overlays: list[dict]
    cross_frames: int
    cross_frame_srcs: list[str]
    offscreen: int = 0
    sequence: int = 0
    new_tabs: list[dict] = field(default_factory=list)
    tabs: list[dict] = field(default_factory=list)
    # The previous observation of the *same page*, used to render a delta.
    previous: "Observation | None" = None

    by_ref: dict[str, Element] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.by_ref = {element.ref: element for element in self.elements}

    @classmethod
    def from_raw(cls, raw: dict, *, mask_secrets=None) -> "Observation":
        elements = []
        for item in raw.get("actions", []):
            secret = bool(item.get("secret"))
            if mask_secrets is not None:
                secret = secret or bool(mask_secrets(item.get("name") or "", item.get("role") or ""))
            elements.append(Element.from_raw(item, secret=secret))
        text = raw.get("text") or ""
        return cls(
            url=raw.get("url") or "",
            title=_short(raw.get("title") or "", 120),
            text=text,
            elements=elements,
            digest=str(hash(raw.get("digest") or "")),
            text_digest=str(hash(text)),
            page_key=raw.get("page_key") or "",
            scroll=raw.get("scroll") or {},
            reachable=int(raw.get("reachable") or 0),
            omitted=int(raw.get("omitted") or 0),
            overlays=raw.get("overlays") or [],
            cross_frames=int(raw.get("cross_frames") or 0),
            cross_frame_srcs=raw.get("cross_frame_srcs") or [],
            # The observer emits this; the header renders it. Reading it here is what makes the
            # "(offscreen N, » = will scroll on act)" note truthful -- it used to be dropped, so
            # the header promised a scroll affordance it never counted.
            offscreen=int(raw.get("offscreen") or 0),
        )

    def find(self, role: str | None = None, name: str | None = None) -> list[Element]:
        needle = (name or "").lower()
        return [
            element for element in self.elements
            if (role is None or element.role == role)
            and (not needle or needle in element.name.lower() or needle in element.label.lower())
        ]

    # --------------------------------------------------------------- renderer

    def render(self, previous: "Observation | None" = None, *, mode: str = "auto",
               include_text: bool = True, focus: list[str] | None = None,
               max_text: int = 4000) -> str:
        """Render for an agent. `mode=auto` emits a delta when it is meaningful."""
        use_delta = mode == "delta" or (
            mode == "auto" and previous is not None and previous.url == self.url
        )
        if use_delta and previous is None:
            use_delta = False

        lines: list[str] = []
        marker = "obs" if not use_delta else "delta"
        header = (f"[{marker}#{self.sequence}] {self.url}  \"{self.title}\"  "
                  f"scroll={self.scroll.get('y', 0)}/{self.scroll.get('height', 0)}  "
                  f"reachable={self.reachable}/{len(self.elements)}")
        if self.omitted:
            header += f"  (omitted {self.omitted} low-priority)"
        if self.offscreen:
            header += f"  (offscreen {self.offscreen}, \u00bb = will scroll on act)"
        triggers = sum(
            1 for element in self.elements
            if element.hoverable and element.expanded != "true"
        )
        if triggers:
            header += f"  ({triggers} \u22ee = menu trigger, hover before choosing)"
        lines.append(header)

        warnings = []
        for overlay in self.overlays:
            warnings.append(f"dialog open: {overlay.get('name') or overlay.get('role')}"
                            + (" [modal]" if overlay.get("modal") else ""))
        if self.cross_frames:
            sample = ", ".join(self.cross_frame_srcs[:2])
            warnings.append(f"{self.cross_frames} cross-origin frame(s) not readable"
                            + (f" ({sample})" if sample else ""))
        if self.new_tabs:
            for tab in self.new_tabs:
                warnings.append(f"NEW TAB opened: {tab.get('url')} \u2192 switch with "
                                f"{{'op':'tab','action':'switch','target_id':'"
                                f"{tab.get('target_id')}'}}")
        lines.extend(f"  ! {warning}" for warning in warnings)

        detail_refs = set(focus or [])

        if use_delta:
            assert previous is not None
            before = {element.ref: element for element in previous.elements}
            after = {element.ref: element for element in self.elements}
            changed = [
                element for ref, element in after.items()
                if ref in before and before[ref].signature() != element.signature()
            ]
            added = [element for ref, element in after.items() if ref not in before]
            gone = [ref for ref in before if ref not in after]
            if not changed and not added and not gone:
                lines.append(f"  = no change ({len(after)} elements)")
            else:
                for element in added:
                    lines.append(f"+ {element.render(detail=element.ref in detail_refs)}")
                for element in changed:
                    old = before[element.ref]
                    note = ""
                    if old.value != element.value and element.editable:
                        note = f"   (was \"{old.value}\")"
                    elif old.occluded != element.occluded:
                        note = "   (now covered)" if element.occluded else "   (now reachable)"
                    lines.append(f"~ {element.render(detail=element.ref in detail_refs)}{note}")
                for ref in gone:
                    lines.append(f"- {ref}  (removed)")
                lines.append(f"  {len(changed)} changed, {len(added)} new, {len(gone)} gone")
        else:
            for element in self.elements:
                lines.append(element.render(detail=element.ref in detail_refs or mode == "full"))
            if not self.elements:
                lines.append("  (no actionable elements visible)")

        if include_text and self.text:
            fresh_text = (previous is None) or (self.text_digest != previous.text_digest)
            if fresh_text:
                lines.append("text:")
                lines.append(_short(self.text, max_text))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "url": self.url,
            "title": self.title,
            "digest": self.digest,
            "page_key": self.page_key,
            "scroll": self.scroll,
            "reachable": self.reachable,
            "elements": len(self.elements),
            "overlays": self.overlays,
            "tabs": self.tabs,
            "json": json.loads(json.dumps({
                "url": self.url, "title": self.title,
                "elements": [
                    {k: v for k, v in element.__dict__.items()
                     if k in {"ref", "role", "name", "value", "editable", "occluded",
                              "checked", "current", "options", "context"}}
                    for element in self.elements
                ],
            })),
        }
