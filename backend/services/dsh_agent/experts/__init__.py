from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).parent
EXPERT_ID = "ecommerce-design"
REFERENCES = {"product-rules": "商品一致性与合规", "style-library": "电商构图与风格"}


def resolve_expert(value: str | None) -> str:
    if value not in (None, "", EXPERT_ID):
        raise ValueError("未知的专家，请重新选择")
    return value or ""


def expert_catalog() -> list[dict]:
    return [{"id": EXPERT_ID, "label": "电商设计专家", "description": "商品主图、白底图、SKU 与营销视觉"}]


@lru_cache(maxsize=3)
def _read(name: str) -> str:
    return (ROOT / "ecommerce_design" / name).read_text(encoding="utf-8")


def expert_policy(expert_id: str) -> str:
    return _read("SKILL.md") if resolve_expert(expert_id) else "当前未选择专家。即使历史中使用过专家，也不要沿用其角色要求；仍须保留用户确认的商品约束和任务记录。"


def read_reference(expert_id: str, args: dict) -> dict:
    if not resolve_expert(expert_id):
        raise ValueError("请先选择电商设计专家")
    key = args.get("reference")
    if set(args) != {"reference"} or not isinstance(key, str) or key not in REFERENCES:
        raise ValueError("只能读取已授权的专家参考资料")
    return {"reference": key, "title": REFERENCES[key], "content": _read(f"references/{key}.md")}
