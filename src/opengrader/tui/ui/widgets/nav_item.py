from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    item_id: str
    label: str
    meta: str = ""
    display_id: str = ""
    description: str = ""
