"""تحميل ملفات البيانات: الكتالوج وخريطة الأصناف والخيارات والسياسة."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from .config import load_json


@dataclass
class Resources:
    catalog: SimpleNamespace
    item_map: dict
    options: dict
    policy: dict

    @classmethod
    def load(cls) -> "Resources":
        cat = load_json("catalog.json")
        return cls(
            catalog=SimpleNamespace(trade_names=cat["trade_names"], finishes=cat["finishes"], shapes=cat["shapes"],
                                    families={f["code"]: f["ar"] for f in cat["families"]},
                                    aliases={k: v for k, v in load_json("aliases.json").items() if not k.startswith("_")}),
            item_map=load_json("item_map.json")["items"],
            options=load_json("options.json"),
            policy=load_json("policy.json"),
        )
