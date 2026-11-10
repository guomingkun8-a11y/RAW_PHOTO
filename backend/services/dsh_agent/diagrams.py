from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

from services.dsh_agent.image_media import media_dict
from services.dsh_agent.store import DshAgentConversationModel, DshAgentMediaModel, DshAgentMessageModel


MAX_SVG_BYTES = 60000
SVG_NS = "http://www.w3.org/2000/svg"
TAGS = {"svg", "g", "defs", "title", "desc", "style", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "path", "text", "tspan", "marker", "clipPath", "linearGradient", "radialGradient", "stop", "use"}
ATTRS = set(("id class style viewBox width height x y x1 y1 x2 y2 cx cy r rx ry d points transform fill fill-opacity "
             "fill-rule stroke stroke-width stroke-opacity stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin "
             "stroke-miterlimit opacity font-family font-size font-weight font-style text-anchor dominant-baseline "
             "alignment-baseline dx dy rotate textLength lengthAdjust preserveAspectRatio marker-start marker-mid marker-end "
             "markerWidth markerHeight markerUnits refX refY orient clip-path clipPathUnits gradientUnits gradientTransform "
             "offset stop-color stop-opacity spreadMethod vector-effect color role aria-label aria-labelledby aria-describedby href").split())


def validate_svg(source: str) -> tuple[str, int, int]:
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > MAX_SVG_BYTES:
        raise ValueError("图示 SVG 不能为空，且不能超过 60000 字节")
    # Refuse DTD/entities before using the standard XML parser; never resolve external resources.
    if "<!" in source or "<?" in source:
        raise ValueError("图示不能包含文档声明、实体或处理指令")
    try:
        root = ET.fromstring(source)
    except (ET.ParseError, RecursionError) as exc:
        raise ValueError("图示必须是有效的独立 SVG") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise ValueError("图示必须包含 SVG 命名空间")
    try:
        bounds = [float(value) for value in re.split(r"[\s,]+", root.attrib.get("viewBox", "").strip())]
        if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds) or not all(1 <= v <= 4096 for v in bounds[2:]):
            raise ValueError()
    except ValueError as exc:
        raise ValueError("图示 viewBox 无效，宽高必须在 1 至 4096 之间") from exc
    elements = list(root.iter())
    if len(elements) > 1600 or not any(node.tag in {f"{{{SVG_NS}}}{tag}" for tag in TAGS - {"svg", "g", "defs", "title", "desc", "style"}} for node in elements):
        raise ValueError("图示为空或元素过多，请拆分图示")
    pending = [(root, 0)]
    while pending:
        node, depth = pending.pop()
        if depth > 48:
            raise ValueError("图示嵌套过深，请简化分组")
        pending.extend((child, depth + 1) for child in node)
    ids = set()
    for node in elements:
        if node.tag not in {f"{{{SVG_NS}}}{tag}" for tag in TAGS}:
            raise ValueError("图示包含不支持的元素，请只使用静态 SVG 基础图形")
        if "id" in node.attrib:
            identifier = node.attrib["id"]
            if not re.fullmatch(r"[A-Za-z_][\w.-]{0,100}", identifier) or identifier in ids:
                raise ValueError("图示元素 ID 无效或重复")
            ids.add(identifier)
        for key, value in node.attrib.items():
            if key not in ATTRS and key != "{http://www.w3.org/XML/1998/namespace}space":
                raise ValueError("图示包含不支持的属性")
            if key == "href" and not re.fullmatch(r"#[A-Za-z_][\w.-]{0,100}", value):
                raise ValueError("图示不允许外部资源")
        paints = list(node.attrib.items())
        if node.tag == f"{{{SVG_NS}}}style":
            paints.append(("stylesheet", node.text or ""))
        for key, value in paints:
            if re.search(r"@|\\|/\*|expression\s*\(|javascript:|data:|https?:|behavior\s*:|-moz-binding", value, re.I):
                raise ValueError("图示不能包含外部资源或可执行样式")
            remainder = re.sub(r"url\(\s*(['\"]?)#[A-Za-z_][\w.-]{0,100}\1\s*\)", "", value, flags=re.I)
            if re.search(r"url\s*\(", remainder, re.I):
                raise ValueError("图示只允许本地图形引用")
            if key in {"style", "stylesheet", "transform", "gradientTransform"} and any(name.lower() not in {"url", "var", "rgb", "rgba", "hsl", "hsla", "calc", "min", "max", "clamp",
                                        "translate", "translatex", "translatey", "scale", "scalex", "scaley", "rotate", "matrix", "skewx", "skewy"}
                   for name in re.findall(r"([A-Za-z_-][A-Za-z0-9_-]*)\s*\(", value)):
                raise ValueError("图示包含不支持的样式函数")
        if node.tag == f"{{{SVG_NS}}}use":
            target = root.find(f".//*[@id='{node.attrib.get('href', '')[1:]}']")
            if target is None or len(target) or target.tag not in {f"{{{SVG_NS}}}{tag}" for tag in ("rect", "circle", "ellipse", "line", "polyline", "polygon", "path")}:
                raise ValueError("图示不允许递归或复杂图形引用")
    width, height = (max(1, math.ceil(value)) for value in bounds[2:])
    root.set("width", str(width))
    root.set("height", str(height))
    ET.register_namespace("", SVG_NS)
    return ET.tostring(root, encoding="unicode"), width, height


class DiagramService:
    def __init__(self, store):
        self.store = store

    def get(self, context, args: dict) -> dict:
        if set(args) != {"reference_id"} or not isinstance(args.get("reference_id"), str):
            raise ValueError("请提供当前会话的图示引用 ID")
        with self.store._session() as session:
            row = session.query(DshAgentMediaModel).filter_by(
                media_id=args["reference_id"], conversation_id=context.conversation_id, owner_id=context.owner_id, kind="diagram",
            ).one_or_none()
            if row is None:
                raise ValueError("图示不存在或不属于当前会话")
            return media_dict(row)

    def render(self, context, call_id: str, args: dict) -> dict:
        if not context.allow_generation:
            raise ValueError("本轮仅继续文字回答，不创建新图示")
        if set(args) != {"title", "svg"} or not isinstance(args.get("title"), str) or not 1 <= len(args["title"].strip()) <= 100:
            raise ValueError("请提供 1 至 100 字的图示标题和 SVG")
        svg, width, height = validate_svg(args.get("svg"))
        payload = {"title": args["title"].strip(), "svg": svg, "width": width, "height": height, "filename": "diagram.svg"}
        digest = hashlib.sha256(f"{context.owner_id}:{context.conversation_id}:{context.message_id}:{call_id}".encode()).hexdigest()
        with self.store._session() as session:
            conversation = session.query(DshAgentConversationModel).filter_by(
                conversation_id=context.conversation_id, owner_id=context.owner_id,
            ).with_for_update().one_or_none()
            latest = session.query(DshAgentMessageModel.message_id).filter_by(
                conversation_id=context.conversation_id, owner_id=context.owner_id, role="user",
            ).order_by(DshAgentMessageModel.created_at.desc(), DshAgentMessageModel.message_id.desc()).first()
            if not conversation or conversation.status != "running" or not latest or latest[0] != context.message_id:
                raise ValueError("图示工具已不属于当前有效轮次")
            rows = session.query(DshAgentMediaModel).filter_by(
                conversation_id=context.conversation_id, owner_id=context.owner_id, message_id=context.message_id, kind="diagram",
            ).all()
            row = next((item for item in rows if item.media_id == f"dshdiagram_{digest}" or json.loads(item.payload_json) == payload), None)
            if row is not None:
                if json.loads(row.payload_json) != payload:
                    raise ValueError("同一次工具调用不能更改图示，请使用新调用另存版本")
            else:
                if len(rows) >= 4:
                    raise ValueError("每轮最多创建 4 张图示，请分轮生成")
                row = DshAgentMediaModel(media_id=f"dshdiagram_{digest}", owner_id=context.owner_id,
                    conversation_id=context.conversation_id, message_id=context.message_id, kind="diagram",
                    payload_json=json.dumps(payload, ensure_ascii=False))
                session.add(row)
                session.commit()
            result = media_dict(row)
        context.on_media(result)
        return {"reference_id": result["media_id"], "title": result["title"], "width": width, "height": height,
                "message": "图示已保存，预览、放大和下载已显示在当前对话中"}
