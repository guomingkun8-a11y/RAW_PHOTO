from __future__ import annotations

import re

HIGH_RISK_CLAIM_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"杀菌率\s*99(?:\.\d+)?\s*%?", re.IGNORECASE), "清洁表现"),
    (re.compile(r"99(?:\.\d+)?\s*%?\s*杀菌率?", re.IGNORECASE), "清洁表现"),
    (re.compile(r"(?:百分之\s*(?:100|99(?:\.\d+)?|百|一百|九十九)|(?:100|99(?:\.\d+)?)\s*%|百分百)", re.IGNORECASE), "高比例"),
    (re.compile(r"杀灭细菌|灭活病毒|抗病毒|医用级|医疗级|消毒|杀菌|医用", re.IGNORECASE), "清洁护理"),
    (re.compile(r"使用前后(?:变化|对比|差异|效果)?", re.IGNORECASE), "不同使用状态展示"),
    (re.compile(r"清洁前后", re.IGNORECASE), "清洁场景展示"),
    (re.compile(r"功效(?:承诺|宣称)?|绝对化功效", re.IGNORECASE), "中性卖点表达"),
    (re.compile(r"虚假(?:榜单|排名|测评结论|用户评价|销量)|虚构(?:榜单|排名|测评结论|用户评价|销量)", re.IGNORECASE), "虚构商业背书"),
    (re.compile(r"医疗(?:承诺|背书|认证)?", re.IGNORECASE), "医疗相关承诺"),
)

IMAGE_PROMPT_COMPLIANCE_GUARD = (
    "合规约束：画面文字只能基于用户输入和可见商品证据；不要新增价格、排名、认证、参数、销量、"
    "百分百/100%/99% 等绝对化或百分比承诺，也不要新增医疗、消杀、抗菌、病毒相关声明。"
)
IMAGE_PROMPT_COMPLIANCE_MARKER = "合规约束："
IMAGE_PROMPT_DIRECTOR_MARKER = "视觉导演策略："
IMAGE_PROMPT_STANDARD_MARKER = "标准 Prompt 约束："
IMAGE_PROMPT_GENERAL_MARKER = "通用视觉约束："
IMAGE_PROMPT_NEGATIVE_MARKER = "负面约束："
IMAGE_PROMPT_REFERENCE_MARKER = "参考图约束："
IMAGE_PROMPT_PRESERVE_MARKER = "主体保真："
IMAGE_PROMPT_TYPOGRAPHY_MARKER = "高级文字排版约束："
IMAGE_PROMPT_TEXT_LANGUAGE_MARKER = "文字语言约束："
IMAGE_LAYOUT_GUARD_PREFIX = "画面结构约束："
BATCH_SINGLE_IMAGE_MARKER = "批量单图执行："
REFERENCE_EDIT_SCENE = "scene_edit"
REFERENCE_EDIT_VISUAL_STYLE = "visual_style_edit"
REFERENCE_EDIT_PRODUCT = "product_attribute_edit"
REFERENCE_EDIT_REPLACE = "product_replace"
REFERENCE_EDIT_AMBIGUOUS = "ambiguous"
STANDARD_PRODUCT_MUTATION_INTENTS = frozenset({REFERENCE_EDIT_PRODUCT, REFERENCE_EDIT_REPLACE})
SUBJECT_POLICY_PRESERVE = "preserve"
SUBJECT_POLICY_MUTATE = "mutate_requested_attributes"
SUBJECT_POLICY_REPLACE = "replace"
IMAGE_SINGLE_LAYOUT_GUARD = (
    f"{IMAGE_LAYOUT_GUARD_PREFIX}只生成一张完整独立图片，不要拼图、分屏、九宫格或多面板。"
)
IMAGE_PROMPT_DIRECTOR_GUARD = (
    f"{IMAGE_PROMPT_DIRECTOR_MARKER}按用户原始提示和当前参考图执行，不套固定电商模板；"
    "创意选择由用户当前需求、图片证据和用户偏好决定。"
    "需要新增画面文字时默认简体中文；只有用户明确要求英文/English 或逐字给出英文原文时才使用英文。"
    "不要编造价格、认证、参数、销量、百分比、医疗/消杀承诺或其它不可验证卖点。"
)
IMAGE_PROMPT_NEGATIVE_GUARD = (
    f"{IMAGE_PROMPT_NEGATIVE_MARKER}避免乱码、错别字、水印、拼图、分屏、多面板、主体畸形、错误透视、"
    "关键识别信息被遮挡，以及虚假百分比、认证、医疗、消杀、抗菌、病毒宣传。"
)
IMAGE_PROMPT_STANDARD_GUARD = (
    f"{IMAGE_PROMPT_STANDARD_MARKER}用户当前输入是最高优先级，按原意直接生成或修改；"
    "不二次改写创意方向，不强制套用品牌大片、海报、淘宝模板、固定背景、固定商品占比或固定文字排版。"
)
IMAGE_PROMPT_STANDARD_TYPOGRAPHY_GUARD = (
    f"{IMAGE_PROMPT_TYPOGRAPHY_MARKER}按用户当前提示生成文字排版；"
    "版式由用户要求和图片证据决定，不强制固定商品占比、文字占比或电商模板。"
    f"{IMAGE_PROMPT_TEXT_LANGUAGE_MARKER}排版语言默认使用简体中文；只有用户明确要求英文/English 或逐字给出英文原文时才使用英文。"
    "不要新增价格、折扣、认证、销量、参数、医疗/消杀功效、百分百、100%、99%等绝对化承诺。"
)
IMAGE_PROMPT_GENERAL_GUARD = (
    f"{IMAGE_PROMPT_GENERAL_MARKER}这是通用创作请求，严格执行用户明确提出的主题、主体、场景、风格、构图、文字和画布比例；"
    "只补充画面完整、清晰和可生成所必需的信息。"
    "不要把请求改写成商品广告、淘宝主图、品牌大片、营销海报或固定电商模块。"
    f"{IMAGE_PROMPT_TEXT_LANGUAGE_MARKER}若用户要求画面文字，默认使用简体中文；只有用户明确要求英文/English 或逐字给出英文原文时才使用英文。"
    "受保护品牌、作品、角色或艺术家只作为意图参考，执行时使用原创化、非可识别表达。"
)
IMAGE_PROMPT_GENERAL_NEGATIVE_GUARD = (
    f"{IMAGE_PROMPT_NEGATIVE_MARKER}避免无关电商排版、营销文案、Logo、官方标识、可识别复刻、拼图、分屏、"
    "多面板、错误文字、乱码、水印、主体裁切、错误透视和畸形结构。"
)
IMAGE_GENERAL_COMPLIANCE_GUARD = (
    "通用合规约束：不要新增违法、危险、医疗诊断或未经证实的功效承诺；"
    "受保护品牌、作品、角色或艺术家使用原创化、非可识别表达。"
)

CHINESE_PROMPT_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
BATCH_MULTI_IMAGE_QUANTITY_RE = re.compile(
    r"(?P<verb>设计|生成|制作|产出|做|出|来|给我|为[^，。！？；;:\n]{1,80}?设计出)\s*"
    r"(?P<count>[2-9]|1\d|[二两三四五六七八九十])\s*"
    r"(?P<unit>张|幅|款|版|组|个)"
    r"(?P<tail>[^，。！？；;:\n]{0,80})",
    re.IGNORECASE,
)
BATCH_MULTI_IMAGE_INTENT_RE = re.compile(
    r"多张|几张|多幅|几幅|多款|几款|多个版本|多种场景|不同场景|不同卖点|不同版本|不同风格"
)
BATCH_VARIATION_LINE_RE = re.compile(
    r"^\s*(?P<number>[1-9]\d?|[一二两三四五六七八九十])[\.\、\)）]\s*(?P<body>.+?)\s*$"
)
BATCH_VARIATION_BODY_RE = re.compile(
    r"^(?:场景|设计|方案|版本|风格|卖点|图片|主图|详情)\s*[一二两三四五六七八九十0-9]*\s*[：:]?",
    re.IGNORECASE,
)


ECOMMERCE_REQUEST_KEYWORDS = (
    "商品", "电商", "淘宝", "天猫", "京东", "拼多多", "小红书", "主图", "详情页", "详情图", "产品图", "产品主图", "产品详情",
    "车图", "卖点", "sku", "店铺", "白底商品", "包装", "瓶身", "瓶型", "罐体", "盒型", "袋型", "标签设计",
    "包装展示", "产品摄影", "商品摄影", "购物链接",
    "taobao", "tmall", "ecommerce", "product photo", "product image", "product photography",
)

TYPOGRAPHY_REQUEST_KEYWORDS = (
    "文字排版", "文字版式", "图文排版", "文案排版", "添加文字", "加文字", "写上文字", "标注文字",
    "加标题", "添加标题", "带文字", "带标题", "主标题", "副标题", "标题", "卖点文案", "价格文案",
    "信息图", "typography", "add text", "headline", "caption",
)

TYPOGRAPHY_NEGATION_TERMS = (
    "不要", "不需要", "无需", "不必", "不加", "别加", "不添加", "禁止", "避免", "去掉", "删除", "取消",
    "不做", "不使用", "不采用", "不生成", "没有", "无",
)
TYPOGRAPHY_CLAUSE_BOUNDARY = re.compile(r"[，。！？；;,\n]|但是|不过|然而|(?:^|\s)but(?:\s|$)", re.IGNORECASE)
ENGLISH_TYPOGRAPHY_NEGATION_RE = re.compile(
    r"(?:不要|不需要|不用|别用|禁止|避免|去掉|取消)\s*(?:使用|采用|生成|新增|增加|添加|补写|扩写|写|写成|显示)?\s*(?:英文|英语)[^，。！？；;,\n]{0,12}|"
    r"(?:no|without|avoid|remove|do\s+not|don't)\s+(?:use\s+)?(?:english|english\s+text|english\s+typography)",
    re.IGNORECASE,
)
ENGLISH_TYPOGRAPHY_DIRECT_RE = re.compile(
    r"\b(?:english\s+(?:text|copy|typography|headline|title|subtitle|caption|words?|letters?|layout|slogan|label)s?|"
    r"english\s+typography|"
    r"in\s+english\s+(?:text|copy|headline|title|subtitle|caption|typography|letters?|words?|layout))\b",
    re.IGNORECASE,
)
ENGLISH_COPY_CONTENT_RE = re.compile(
    r"(?:标题|副标题|文案|文字|卖点|角标|字幕|headline|title|subtitle|caption|copy|text)\s*"
    r"(?:写|写上|显示|展示|使用|用|为|是|叫|内容是|文案是|标题是|[:：])\s*[\"'“”‘’]?\s*[A-Za-z]|"
    r"(?:写上|显示|展示|添加|加上)\s*[\"'“”‘’]?\s*[A-Za-z][^，。！？；;,\n]{0,80}",
    re.IGNORECASE,
)


def has_explicit_english_typography_request(prompt: str) -> bool:
    text = str(prompt or "").strip()
    if not text:
        return False
    candidate = ENGLISH_TYPOGRAPHY_NEGATION_RE.sub(" ", text)
    if ENGLISH_TYPOGRAPHY_DIRECT_RE.search(candidate):
        return True
    if re.search(r"(?:英文|英语)\s*(?:排版|文案|标题|副标题|卖点|角标|字幕|copy|text|headline|title|subtitle|caption|layout|typography)", candidate, re.IGNORECASE):
        return True
    if re.search(r"(?:排版|标题|副标题|文案|卖点|角标|字幕|copy|text|headline|title|subtitle|caption|layout|typography).{0,20}\benglish\b", candidate, re.IGNORECASE):
        return True
    if ENGLISH_COPY_CONTENT_RE.search(candidate):
        return True
    return False


def is_ecommerce_request(
    prompt: str,
    product: object | None = None,
    *,
    has_reference: bool = False,
    platform: str = "",
) -> bool:
    """Route only explicit commercial-product work into the ecommerce engine.

    A reference image alone is not enough: a user may upload a person, artwork,
    room, or game screenshot for a general edit.
    """
    text = f"{prompt} {platform}".strip().lower()
    if any(keyword in text for keyword in ECOMMERCE_REQUEST_KEYWORDS):
        return True
    if isinstance(product, dict) and any(
        str(product.get(key) or "").strip()
        for key in ("name", "sku", "brand", "category", "selling_points", "sellingPoints")
    ):
        return True
    return False


def has_typography_request(prompt: str) -> bool:
    text = str(prompt or "").strip()
    if not text:
        return False
    lowered = text.lower()
    for keyword in TYPOGRAPHY_REQUEST_KEYWORDS:
        normalized = keyword.lower()
        for match in re.finditer(re.escape(normalized), lowered):
            prefix = TYPOGRAPHY_CLAUSE_BOUNDARY.split(lowered[max(0, match.start() - 28):match.start()])[-1]
            chinese_negated = any(
                re.search(rf"{re.escape(term)}[^，。！？；;,\n]{{0,12}}$", prefix)
                for term in TYPOGRAPHY_NEGATION_TERMS
            )
            english_negated = bool(re.search(r"(?:\bno|\bwithout|\bavoid|\bremove)\s+(?:\w+\s+){0,3}$", prefix))
            if not chinese_negated and not english_negated:
                return True
    return False


def normalize_prompt_engine_mode(value: object, default: str = "professional") -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "standard":
        return "standard"
    if normalized in {"general", "adaptive", "creative", "通用", "自适应"}:
        return "general"
    return "professional" if default not in {"standard", "general"} else default


def strip_high_risk_claims(text: str) -> str:
    cleaned = str(text or "")
    for pattern, replacement in HIGH_RISK_CLAIM_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned.strip()


def classify_reference_edit_intent(text: str) -> str:
    """Classify standard-mode reference edits without adding an LLM round trip."""

    lowered = str(text or "").strip().lower()
    compact = re.sub(r"[\s，。！？、,.;；:：的]+", "", lowered)
    if not compact:
        return REFERENCE_EDIT_SCENE

    replace_phrases = (
        "替换原商品",
        "替换掉原商品",
        "把原商品换成",
        "把原产品换成",
        "换成新商品",
        "换成这个商品",
        "换成上传的商品",
        "使用最新上传商品",
        "用新商品替换",
        "replaceproduct",
    )
    if any(phrase in compact for phrase in replace_phrases):
        return REFERENCE_EDIT_REPLACE

    mutation_terms = (
        "修改",
        "改一下",
        "改一个",
        "改个",
        "改成",
        "变成",
        "更换",
        "重新设计",
        "换成",
        "换个",
        "换一个",
        "换一种",
        "换一款",
        "换一版",
        "adjust",
        "change",
        "redesign",
    )
    product_specific_terms = (
        "商品样式",
        "产品样式",
        "商品款式",
        "产品款式",
        "商品外观",
        "产品外观",
        "包装",
        "瓶型",
        "瓶身",
        "瓶盖",
        "罐体",
        "盒型",
        "袋型",
        "外壳",
        "productstyle",
        "packaging",
    )
    product_subject_terms = (
        "商品", "产品", "瓶子", "瓶身", "瓶", "罐子", "罐体", "罐", "盒子", "包装盒", "盒", "袋子", "包装袋", "袋",
        "product", "bottle", "package", "packaging",
    )
    generic_attribute_terms = (
        "样子", "样式", "款式", "外观", "设计", "造型", "形状", "结构", "颜色", "材质", "图案", "标签",
        "style", "appearance", "shape", "color", "material", "pattern", "label",
    )
    has_mutation = any(term in compact for term in mutation_terms)
    has_scoped_attribute = any(
        f"{subject}{attribute}" in compact
        for subject in product_subject_terms
        for attribute in generic_attribute_terms
    )
    has_direct_container_change = any(
        f"{mutation}{subject}" in compact
        for mutation in mutation_terms
        for subject in ("瓶子", "瓶身", "瓶", "罐子", "罐体", "罐", "盒子", "盒型", "袋子", "袋型")
    )
    if has_mutation and (any(term in compact for term in product_specific_terms) or has_scoped_attribute or has_direct_container_change):
        return REFERENCE_EDIT_PRODUCT

    visual_style_terms = (
        "图片风格",
        "画面风格",
        "视觉风格",
        "摄影风格",
        "设计风格",
        "海报风格",
        "色调风格",
        "photographystyle",
        "visualstyle",
        "imagestyle",
    )
    if any(term in compact for term in visual_style_terms):
        return REFERENCE_EDIT_VISUAL_STYLE

    scene_terms = (
        "背景",
        "场景",
        "环境",
        "台面",
        "桌面",
        "光线",
        "光影",
        "构图",
        "机位",
        "镜头",
        "摆放",
        "道具",
        "background",
        "scene",
        "lighting",
        "composition",
    )
    if any(term in compact for term in scene_terms):
        return REFERENCE_EDIT_SCENE

    if has_mutation or any(term in compact for term in ("换个样式", "换一种样式", "改个风格", "换个风格")):
        return REFERENCE_EDIT_AMBIGUOUS
    return REFERENCE_EDIT_SCENE


def standard_edit_allows_product_mutation(intent: object) -> bool:
    return str(intent or "").strip() in STANDARD_PRODUCT_MUTATION_INTENTS


def normalize_subject_mutation_policy(value: object, default: str = SUBJECT_POLICY_PRESERVE) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "keep": SUBJECT_POLICY_PRESERVE,
        "preserve": SUBJECT_POLICY_PRESERVE,
        "mutate": SUBJECT_POLICY_MUTATE,
        "modify": SUBJECT_POLICY_MUTATE,
        "mutate_requested_attributes": SUBJECT_POLICY_MUTATE,
        "replace": SUBJECT_POLICY_REPLACE,
    }.get(normalized, default)


def _append_guard_once(text: str, guard: str, marker: str) -> str:
    cleaned = text.strip()
    if marker in cleaned:
        return cleaned
    return f"{cleaned}\n\n{guard}".strip()


def _strip_existing_guards(text: str) -> str:
    cleaned = str(text or "").strip()
    positions: list[int] = []
    for marker in (
        IMAGE_PROMPT_DIRECTOR_MARKER,
        IMAGE_PROMPT_STANDARD_MARKER,
        IMAGE_PROMPT_GENERAL_MARKER,
        IMAGE_PROMPT_TYPOGRAPHY_MARKER,
        IMAGE_PROMPT_TEXT_LANGUAGE_MARKER,
        IMAGE_LAYOUT_GUARD_PREFIX,
        IMAGE_PROMPT_COMPLIANCE_MARKER,
    ):
        position = cleaned.find(marker)
        if position >= 0:
            positions.append(position)
    if not positions:
        return cleaned
    return cleaned[: min(positions)].strip()


def _prompt_number_value(value: str) -> int:
    stripped = str(value or "").strip()
    if stripped.isdigit():
        return int(stripped)
    return CHINESE_PROMPT_NUMBERS.get(stripped, 0)


def _singular_unit(unit: str) -> str:
    return unit if unit in {"张", "幅", "款", "版"} else "张"


def _rewrite_batch_quantity(match: re.Match[str]) -> str:
    verb = str(match.group("verb") or "")
    unit = _singular_unit(str(match.group("unit") or "张"))
    tail = str(match.group("tail") or "")
    return f"{verb}一{unit}{tail}（当前这一{unit}独立成品图）"


def _variation_body(text: str) -> str:
    body = BATCH_VARIATION_BODY_RE.sub("", str(text or "")).strip()
    return body or str(text or "").strip()


def _is_batch_variation_body(text: str) -> bool:
    return bool(BATCH_VARIATION_BODY_RE.match(str(text or "").strip()))


def _select_batch_variation(text: str, *, image_count: int, image_index: int) -> tuple[str, str]:
    lines = text.splitlines()
    total = max(1, int(image_count or 1))
    current = min(total, max(1, int(image_index or 0) + 1))
    entries: list[tuple[int, int, str]] = []
    for line_index, line in enumerate(lines):
        match = BATCH_VARIATION_LINE_RE.match(line)
        if not match:
            continue
        body = str(match.group("body") or "")
        if not _is_batch_variation_body(body):
            continue
        number = _prompt_number_value(match.group("number"))
        if 1 <= number <= max(total, 20):
            entries.append((line_index, number, _variation_body(body)))
    if len(entries) < 2:
        return text, ""

    selected = next((entry for entry in entries if entry[1] == current), None)
    if selected is None:
        selected = entries[(current - 1) % len(entries)]
    numbered_line_indexes = {entry[0] for entry in entries}
    stripped_lines = [
        line for line_index, line in enumerate(lines)
        if line_index not in numbered_line_indexes
    ]
    stripped_text = re.sub(r"\n{3,}", "\n\n", "\n".join(stripped_lines)).strip()
    return stripped_text, selected[2]


def collapse_batch_prompt_to_single_image(text: str, *, image_count: int = 1, image_index: int = 0) -> str:
    total = max(1, int(image_count or 1))
    if total <= 1:
        return str(text or "").strip()

    current = min(total, max(1, int(image_index or 0) + 1))
    cleaned = str(text or "").strip()
    if BATCH_SINGLE_IMAGE_MARKER in cleaned:
        return cleaned
    rewritten = BATCH_MULTI_IMAGE_QUANTITY_RE.sub(_rewrite_batch_quantity, cleaned)
    rewritten = re.sub(
        r"(?P<verb>设计|生成|制作|产出|做|出|来|给我)\s*"
        r"(?:多张|几张|多幅|几幅|多款|几款|多个版本|多种场景|不同场景|不同卖点|不同版本|不同风格)",
        r"\g<verb>当前这一张独立",
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten, selected_variation = _select_batch_variation(
        rewritten,
        image_count=total,
        image_index=current - 1,
    )
    has_batch_conflict = rewritten != cleaned or bool(selected_variation) or bool(BATCH_MULTI_IMAGE_INTENT_RE.search(cleaned))
    if not has_batch_conflict:
        return cleaned

    prefix = (
        f"{BATCH_SINGLE_IMAGE_MARKER}本请求已拆分为 {total} 个独立任务；当前是第 {current}/{total} 张。"
        "忽略原文中的多张、多款、多场景或完整清单要求，只输出当前这一张独立完整成品图，不要合集、拼图或分屏。"
    )
    parts = [prefix]
    if selected_variation:
        parts.append(f"当前这一张的具体方向：{selected_variation}")
    if rewritten:
        parts.append(rewritten)
    return "\n\n".join(parts).strip()


def image_layout_guard(*, image_count: int = 1, image_index: int = 0) -> str:
    total = max(1, int(image_count or 1))
    if total <= 1:
        return IMAGE_SINGLE_LAYOUT_GUARD
    current = min(total, max(1, int(image_index or 0) + 1))
    return (
        f"{IMAGE_LAYOUT_GUARD_PREFIX}这是第 {current}/{total} 张独立成品图；只生成这一张，"
        "可做构图、背景或光线差异，但不要拼图、分屏、九宫格或多面板。"
    )


def image_director_guard(
    *,
    image_count: int = 1,
    image_index: int = 0,
    has_reference: bool = False,
    preserve_subject: bool = False,
    subject_mutation_policy: str = SUBJECT_POLICY_PRESERVE,
) -> str:
    parts = [IMAGE_PROMPT_DIRECTOR_GUARD]
    if max(1, int(image_count or 1)) > 1:
        current = min(max(1, int(image_count or 1)), max(1, int(image_index or 0) + 1))
        parts.append(f"- 批量差异：第 {current}/{max(1, int(image_count or 1))} 张可采用不同构图、背景或光线，但仍按用户同一目标执行。")
    if has_reference:
        policy = normalize_subject_mutation_policy(subject_mutation_policy)
        if policy == SUBJECT_POLICY_MUTATE:
            parts.append(
                f"- {IMAGE_PROMPT_REFERENCE_MARKER}允许修改用户明确指定的商品属性；未指定的品牌、Logo、可见文字、品类和识别特征保持可信。"
            )
        elif policy == SUBJECT_POLICY_REPLACE:
            parts.append(
                f"- {IMAGE_PROMPT_REFERENCE_MARKER}使用用户指明或最新上传的商品作为目标主体，不继承旧商品的款式、包装、颜色、Logo 或文字。"
            )
        else:
            parts.append(
                f"- {IMAGE_PROMPT_REFERENCE_MARKER}以参考图商品为主体来源，保持外形、包装结构、Logo、可见文字、颜色、材质和比例；"
                "只按用户要求改变场景、构图、光线或风格。"
            )
    if preserve_subject:
        parts.append(f"- {IMAGE_PROMPT_PRESERVE_MARKER}优先保证商品身份准确，不牺牲关键结构、包装、文字和识别元素。")
    parts.append(IMAGE_PROMPT_NEGATIVE_GUARD)
    return "\n".join(parts)


def image_general_guard(
    *,
    has_reference: bool = False,
    preserve_subject: bool = False,
) -> str:
    parts = [IMAGE_PROMPT_GENERAL_GUARD]
    if has_reference:
        parts.append(
            f"- {IMAGE_PROMPT_REFERENCE_MARKER}以参考图主体或关键视觉元素为依据；只按用户明确要求修改。"
        )
    if preserve_subject:
        parts.append(f"- {IMAGE_PROMPT_PRESERVE_MARKER}优先保持参考主体的身份、轮廓、比例、材质和关键细节可信。")
    parts.append(IMAGE_PROMPT_GENERAL_NEGATIVE_GUARD)
    return "\n".join(parts)


def image_standard_guard(
    *,
    has_reference: bool = False,
    preserve_subject: bool = False,
    reference_edit_intent: str = REFERENCE_EDIT_SCENE,
    needs_typography: bool = False,
) -> str:
    parts = [IMAGE_PROMPT_STANDARD_GUARD]
    if needs_typography:
        parts.append(IMAGE_PROMPT_STANDARD_TYPOGRAPHY_GUARD)
    if has_reference:
        if reference_edit_intent == REFERENCE_EDIT_PRODUCT:
            parts.append(
                f"- {IMAGE_PROMPT_REFERENCE_MARKER}按用户原始提示修改明确指定的商品款式、外观、包装、颜色、材质、形状或结构；"
                "未要求修改的商品识别特征尽量保持不变。"
            )
        elif reference_edit_intent == REFERENCE_EDIT_REPLACE:
            parts.append(
                f"- {IMAGE_PROMPT_REFERENCE_MARKER}按用户原始提示完成商品替换，以用户指明或最新上传的商品作为目标主体。"
            )
        elif reference_edit_intent == REFERENCE_EDIT_VISUAL_STYLE:
            parts.append(
                f"- {IMAGE_PROMPT_REFERENCE_MARKER}保持参考图商品身份，只按用户要求修改摄影风格、色调、光影或视觉表现。"
            )
        elif reference_edit_intent == REFERENCE_EDIT_AMBIGUOUS:
            parts.append(
                f"- {IMAGE_PROMPT_REFERENCE_MARKER}忠实执行用户对“样式”或“风格”的字面要求，不自动扩写成淘宝主图或擅自加字。"
            )
        else:
            parts.append(
                f"- {IMAGE_PROMPT_REFERENCE_MARKER}用户未要求修改商品时，保持参考图商品身份；按用户要求修改背景、构图、道具、机位或光线。"
            )
    if preserve_subject and reference_edit_intent != REFERENCE_EDIT_AMBIGUOUS:
        parts.append(f"- {IMAGE_PROMPT_PRESERVE_MARKER}保留用户未要求修改的商品身份、包装文字和关键识别元素。")
    return "\n".join(parts)


def sanitize_image_prompt(
    text: str,
    *,
    image_count: int = 1,
    image_index: int = 0,
    has_reference: bool = False,
    preserve_subject: bool = False,
    prompt_engine_mode: str = "professional",
    subject_mutation_policy: str = SUBJECT_POLICY_PRESERVE,
) -> str:
    cleaned = strip_high_risk_claims(_strip_existing_guards(text))
    cleaned = collapse_batch_prompt_to_single_image(
        cleaned,
        image_count=image_count,
        image_index=image_index,
    )
    mode = normalize_prompt_engine_mode(prompt_engine_mode)
    if mode == "professional" and not is_ecommerce_request(cleaned, has_reference=has_reference):
        mode = "general"
    reference_edit_intent = classify_reference_edit_intent(cleaned) if mode == "standard" and has_reference else REFERENCE_EDIT_SCENE
    effective_preserve_subject = preserve_subject and not standard_edit_allows_product_mutation(reference_edit_intent)
    if mode == "general":
        guard = image_general_guard(has_reference=has_reference, preserve_subject=preserve_subject)
        marker = IMAGE_PROMPT_GENERAL_MARKER
    elif mode == "standard":
        guard = image_standard_guard(
            has_reference=has_reference,
            preserve_subject=effective_preserve_subject,
            reference_edit_intent=reference_edit_intent,
            needs_typography=has_typography_request(cleaned),
        )
        marker = IMAGE_PROMPT_STANDARD_MARKER
    else:
        guard = image_director_guard(
            image_count=image_count,
            image_index=image_index,
            has_reference=has_reference,
            preserve_subject=preserve_subject,
            subject_mutation_policy=subject_mutation_policy,
        )
        marker = IMAGE_PROMPT_DIRECTOR_MARKER
    cleaned = _append_guard_once(cleaned, guard, marker)
    cleaned = _append_guard_once(
        cleaned,
        image_layout_guard(image_count=image_count, image_index=image_index),
        IMAGE_LAYOUT_GUARD_PREFIX,
    )
    if mode == "general":
        return _append_guard_once(cleaned, IMAGE_GENERAL_COMPLIANCE_GUARD, IMAGE_PROMPT_COMPLIANCE_MARKER)
    return _append_guard_once(cleaned, IMAGE_PROMPT_COMPLIANCE_GUARD, IMAGE_PROMPT_COMPLIANCE_MARKER)


def ensure_image_prompt_engineered(
    text: str,
    *,
    image_count: int = 1,
    image_index: int = 0,
    has_reference: bool = False,
    preserve_subject: bool = False,
    prompt_engine_mode: str = "professional",
    subject_mutation_policy: str = SUBJECT_POLICY_PRESERVE,
) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    mode = normalize_prompt_engine_mode(prompt_engine_mode)
    if mode == "professional" and not is_ecommerce_request(cleaned, has_reference=has_reference):
        mode = "general"
    if IMAGE_PROMPT_STANDARD_MARKER in cleaned:
        needs_typography = mode == "standard" and has_typography_request(cleaned) and IMAGE_PROMPT_TYPOGRAPHY_MARKER not in cleaned
        needs_reference = has_reference and IMAGE_PROMPT_REFERENCE_MARKER not in cleaned
        needs_preserve = preserve_subject and IMAGE_PROMPT_PRESERVE_MARKER not in cleaned
        if needs_typography or needs_reference or needs_preserve:
            return sanitize_image_prompt(
                cleaned,
                image_count=image_count,
                image_index=image_index,
                has_reference=has_reference,
                preserve_subject=preserve_subject,
                prompt_engine_mode="standard",
                subject_mutation_policy=subject_mutation_policy,
            )
        return cleaned
    if IMAGE_PROMPT_DIRECTOR_MARKER in cleaned:
        needs_reference = has_reference and IMAGE_PROMPT_REFERENCE_MARKER not in cleaned
        needs_preserve = preserve_subject and IMAGE_PROMPT_PRESERVE_MARKER not in cleaned
        if needs_reference or needs_preserve:
            return sanitize_image_prompt(
                cleaned,
                image_count=image_count,
                image_index=image_index,
                has_reference=has_reference,
                preserve_subject=preserve_subject,
                prompt_engine_mode=mode,
                subject_mutation_policy=subject_mutation_policy,
            )
        return cleaned
    if IMAGE_PROMPT_GENERAL_MARKER in cleaned:
        needs_reference = has_reference and IMAGE_PROMPT_REFERENCE_MARKER not in cleaned
        needs_preserve = preserve_subject and IMAGE_PROMPT_PRESERVE_MARKER not in cleaned
        if needs_reference or needs_preserve:
            return sanitize_image_prompt(
                cleaned,
                image_count=image_count,
                image_index=image_index,
                has_reference=has_reference,
                preserve_subject=preserve_subject,
                prompt_engine_mode="general",
                subject_mutation_policy=subject_mutation_policy,
            )
        return cleaned
    return sanitize_image_prompt(
        cleaned,
        image_count=image_count,
        image_index=image_index,
        has_reference=has_reference,
        preserve_subject=preserve_subject,
        prompt_engine_mode=mode,
        subject_mutation_policy=subject_mutation_policy,
    )
