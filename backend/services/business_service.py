from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Any

from PIL import Image
from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, and_, create_engine, desc, or_, text
from sqlalchemy.orm import declarative_base, sessionmaker

from services.cache_utils import TTLCache
from services.config import config
from services.image_service import thumbnail_url
from services.image_storage_service import image_storage_service

Base = declarative_base()

DEFAULT_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/raw_photo?charset=utf8mb4"


def _pk_type():
    return BigInteger().with_variant(Integer, "sqlite")


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _database_url() -> str:
    return (
        os.getenv("IMAGE_LIBRARY_DATABASE_URL")
        or os.getenv("MYSQL_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _now() -> datetime:
    return datetime.now()


def _image_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            return image.size
    except Exception:
        return None, None


class ProductModel(Base):
    __tablename__ = "business_products"

    id = Column(_pk_type(), primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    created_by = Column(String(191), nullable=False, default="local-admin")
    name = Column(String(191), nullable=False)
    sku = Column(String(191), nullable=True)
    brand = Column(String(191), nullable=True)
    category = Column(String(191), nullable=True)
    selling_points = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class ProductReferenceModel(Base):
    __tablename__ = "business_product_references"

    id = Column(_pk_type(), primary_key=True, autoincrement=True)
    product_id = Column(BigInteger, nullable=False)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    file_name = Column(String(255), nullable=True)
    mime_type = Column(String(128), nullable=True)
    image_rel = Column(String(512), nullable=False)
    image_url = Column(String(1024), nullable=False)
    thumbnail_url = Column(String(1024), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    storage = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class PromptTemplateModel(Base):
    __tablename__ = "business_prompt_templates"

    id = Column(_pk_type(), primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    created_by = Column(String(191), nullable=False, default="local-admin")
    name = Column(String(191), nullable=False)
    category = Column(String(64), nullable=False, default="main")
    content = Column(Text, nullable=False)
    model = Column(String(191), nullable=True)
    size = Column(String(64), nullable=True)
    quality = Column(String(64), nullable=True)
    preserve_subject = Column(Integer, nullable=False, default=0)
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class AuditLogModel(Base):
    __tablename__ = "business_audit_logs"

    id = Column(_pk_type(), primary_key=True, autoincrement=True)
    owner_id = Column(String(191), nullable=False, default="local-admin")
    actor_id = Column(String(191), nullable=False, default="local-admin")
    action = Column(String(64), nullable=False)
    target_type = Column(String(64), nullable=False)
    target_id = Column(String(191), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


DEFAULT_TEMPLATES = [
    {
        "name": "电商主图",
        "category": "main",
        "content": (
            "你是资深电商视觉策略师，请基于用户补充要求和参考商品，生成一张高转化商品主图。\n"
            "目标：让消费者在搜索结果、店铺首页和商品详情首屏中一眼识别商品，并产生点击欲望。\n"
            "画面要求：商品必须是绝对主体，占画面 70%-85%，结构完整、边缘清晰、比例真实；保留商品原有 Logo、包装文字、颜色、材质、纹理、接口、配件和关键识别点，不改变品牌与型号。\n"
            "构图要求：使用正面或 3/4 角度商业摄影构图，主体居中略偏上，留出适度呼吸感；背景干净高级，可使用浅灰、柔和渐变、平台感光影或轻量场景氛围，但不要抢占主体。\n"
            "光影质感：使用柔和棚拍灯光，突出材质反光、金属/塑料/玻璃/织物纹理和产品轮廓，阴影自然落地，画面清爽可信。\n"
            "电商规范：不要新增虚假文字、价格、促销标签、水印、夸张承诺、无关装饰和额外品牌元素；如需要文案区域，仅预留干净留白，不生成难以辨认的小字。\n"
            "输出风格：专业、真实、干净、高级，适合主流电商平台主图审核与投放。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "白底图",
        "category": "white",
        "content": (
            "你是电商平台图片审核与商品陈列专家，请生成一张标准商品白底图。\n"
            "目标：用于 SKU 主图、货架图、平台报名图或商品库素材，强调真实、清晰、可审核。\n"
            "商品要求：完整展示商品，不裁切主体，不变形，不拉伸；严格保留参考商品的外观轮廓、颜色、Logo、包装文字、材质纹理、配件数量和细节位置。\n"
            "背景要求：纯白或接近纯白背景，干净无杂物、无场景、无道具、无装饰图形；商品边缘抠图自然，不出现毛边、脏边、锯齿、残影或明显 AI 修补痕迹。\n"
            "构图要求：商品居中，大小占画面 75%-88%，上下左右留边均衡，适合平台缩略图浏览；可保留极轻微自然接触阴影，让商品不悬浮。\n"
            "光线要求：均匀柔光，准确还原颜色和材质，不要过曝、偏色、强烈反光或过度磨皮。\n"
            "禁止：不要添加文字卖点、价格、标签、边框、人物、手、复杂阴影、额外配件和非商品原有元素。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "场景图",
        "category": "scene",
        "content": (
            "你是资深电商内容策划和商业摄影指导，请生成一张有销售转化力的商品场景图。\n"
            "目标：让消费者快速理解商品的使用场景、尺寸感、生活方式价值和购买理由。\n"
            "主体要求：商品必须保持与参考图一致，包括品牌 Logo、包装文字、形状、颜色、材质、比例和关键结构；商品在画面中仍然是第一视觉焦点，不被道具遮挡。\n"
            "场景要求：根据商品品类选择真实可信的使用环境，例如家居、厨房、浴室、办公桌、户外、车内、美妆台、母婴、运动或礼赠场景；场景要服务商品卖点，不要堆砌无关道具。\n"
            "构图要求：使用商业摄影视角，可采用浅景深、前后层次、自然留白和轻量道具；画面要适合电商详情页、信息流广告和社媒种草封面裁切。\n"
            "光影质感：真实自然光或高级棚拍光，材质清晰，色彩统一，整体干净明亮，有生活感但不杂乱。\n"
            "电商策略：突出一个核心购买理由，例如质感、容量、便携、清洁力、舒适度、礼盒感、专业感或适用人群；不要生成夸张承诺、价格、促销字、虚假认证和水印。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "详情页卖点图",
        "category": "detail",
        "content": (
            "你是电商详情页转化专家，请生成一张适合详情页使用的商品卖点视觉图。\n"
            "目标：用一张图清楚表达商品的核心卖点、材质细节、使用价值和信任感，适合放在详情页首屏下方或卖点模块中。\n"
            "商品要求：商品主体必须与参考图一致，保留 Logo、包装文字、外观结构、颜色、材质、纹理、边缘和关键配件；不要改变品牌信息，不要虚构功能部件。\n"
            "卖点表达：围绕 1-3 个核心卖点组织画面，例如材质工艺、容量尺寸、功能结构、不同使用状态展示、便携收纳、安装方式、适用场景、礼盒包装或人群需求；卖点要视觉化，不要只靠文字。\n"
            "画面结构：使用清晰的信息层级，可包含主商品大图、局部特写、功能分解、材质微距、场景小窗或对比区域；预留干净文本区方便后期设计师添加标题和标注。\n"
            "视觉风格：专业电商详情页风格，背景简洁，光影真实，重点突出，适合移动端浏览；缩略图也能看清主体和卖点。\n"
            "禁止：不要生成无法辨认的小字、虚假参数、价格、活动标签、夸张承诺、医疗/认证承诺、水印和无关装饰。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "小红书种草封面图",
        "category": "小红书",
        "content": (
            "你是资深小红书电商内容策略专家，请生成一张适合小红书笔记首图的商品种草封面。\n"
            "目标：在信息流里提高停留和点击，让用户第一眼知道这是什么商品、适合谁、对应什么使用需求，同时保持真实生活分享感，不像硬广海报。\n"
            "主体要求：商品必须保持与参考图一致，保留品牌 Logo、包装文字、颜色、材质、结构比例、配件和核心识别点；商品是第一视觉焦点，画面缩小后仍能看清品类和质感。\n"
            "封面策略：围绕一个明确种草角度组织画面，例如颜值质感、不同使用状态展示、通勤便携、轻松上手、礼物推荐、家居氛围、日常仪式感、收纳效率或简洁高级感。\n"
            "构图要求：使用小红书高点击封面构图，主体占画面 55%-75%，可以采用桌面摆拍、手持、半场景、局部特写叠主商品的方式；预留顶部或侧边干净标题区域，方便后期添加 8-14 字封面标题。\n"
            "视觉风格：自然光、干净明亮、生活化但有审美，色彩不过度饱和，背景道具少而准；画面要像真实博主认真拍摄的优质内容，而不是廉价促销图。\n"
            "合规边界：不要生成价格、促销标签、夸张承诺、虚构排名、虚构结论、水印、二维码和难以辨认的小字。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1365",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "小红书生活方式场景图",
        "category": "小红书",
        "content": (
            "你是小红书电商种草策划和生活方式摄影指导，请生成一张真实、有代入感、能自然带货的商品场景图。\n"
            "目标：让用户快速想象自己拥有和使用商品后的状态，弱化广告感，强化生活方式价值和购买理由。\n"
            "主体要求：商品外观必须与参考图一致，保留 Logo、包装文字、颜色、材质、纹理、结构、尺寸比例和关键细节；商品应自然融入场景但不能被遮挡或弱化。\n"
            "场景选择：根据商品品类选择小红书常见高转化场景，例如卧室床头、客厅茶几、厨房台面、浴室洗手台、化妆桌、办公桌、健身包、车内、旅行行李箱、咖啡店桌面或礼物包装场景。\n"
            "内容策略：画面只突出一个核心使用理由，例如提升效率、变好看、节省空间、出门方便、仪式感、舒适体验、精致送礼、适合新手或适合特定人群。\n"
            "构图光影：使用真实自然光或柔和室内光，轻微浅景深，保留生活痕迹但不杂乱；道具必须服务商品卖点，避免堆满无关装饰。\n"
            "输出要求：画面干净高级、真实可信、适合小红书笔记正文配图和封面裁切；不要生成夸张承诺、价格、活动标签、虚假认证、水印和大段文字。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1365",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "小红书测评对比图",
        "category": "小红书",
        "content": (
            "你是小红书电商测评内容专家，请生成一张适合笔记使用的商品测评对比图。\n"
            "目标：通过清晰的视觉对比帮助用户理解商品特点，建立选择信心，但表达必须克制真实，不做夸张承诺。\n"
            "主体要求：商品必须与参考图保持一致，保留品牌、包装、颜色、材质、结构、比例和关键识别点；对比画面中的商品角度和光线尽量统一，避免因为拍摄差异造成误导。\n"
            "对比方向：可根据品类选择 1 个主要对比维度，例如不同使用状态、新旧款、普通款与升级款、收纳状态、材质细节、容量大小、上身/上脸/上桌呈现、清洁场景或不同场景适配。\n"
            "信息结构：画面可以采用左右分屏、上下分屏、主图加局部放大、细节标注区或 2-3 宫格对比；预留简洁文字位置，方便后期添加短标题、箭头和要点标注。\n"
            "视觉要求：整体干净明亮，层级清楚，差异一眼可见；重点放在真实细节和使用体验，不做廉价夸张的冲击效果。\n"
            "禁止：不要生成绝对化结论、虚构数据、医疗相关承诺、竞品商标攻击、价格、促销标签、水印和密集小字。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1365",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "小红书开箱手持图",
        "category": "小红书",
        "content": (
            "你是小红书电商开箱内容策划和实拍摄影指导，请生成一张具有真实博主分享感的商品开箱或手持图。\n"
            "目标：用真实、亲近、可信的画面降低广告感，让用户感受到商品到手后的质感、大小、包装完整度和使用期待。\n"
            "主体要求：商品、包装、Logo、文字、颜色、材质、结构和配件必须与参考图一致；手持或开箱动作只能辅助展示，不要遮挡关键卖点和品牌识别点。\n"
            "画面内容：可以呈现拆开包装、桌面开箱、手持近拍、包内携带、礼盒打开、配件平铺或刚收到商品的自然摆放状态；保留适度生活痕迹，例如桌布、剪刀、纸袋、卡片或日常桌面，但要干净有秩序。\n"
            "构图要求：视角像真实用户拍摄，可用俯拍、45 度侧拍或手持近景；商品占画面 50%-70%，质感细节清楚，包装层次完整，适合小红书笔记配图。\n"
            "光影风格：自然窗光或柔和室内光，色彩真实，不要过度磨皮、过曝、过饱和；整体亲切、有质感、可信赖。\n"
            "合规边界：不要生成虚假订单信息、快递面单、真实个人隐私、价格标签、夸张促销字、水印和不可辨认的小字。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1365",
        "quality": "high",
        "preserve_subject": 1,
    },
    {
        "name": "小红书卖点信息卡",
        "category": "小红书",
        "content": (
            "你是小红书电商转化专家和信息视觉设计顾问，请生成一张适合笔记正文的商品卖点信息卡。\n"
            "目标：用小红书用户容易理解的方式，把商品 3-5 个核心卖点讲清楚，适合收藏、转发和辅助下单决策。\n"
            "主体要求：商品主体必须与参考图一致，保留 Logo、包装文字、外观结构、颜色、材质、纹理、配件和关键细节；商品应占据清晰主视觉位置，不被信息区压住。\n"
            "信息策略：围绕真实可感知卖点组织画面，例如适合人群、使用场景、材质工艺、容量尺寸、便携收纳、肤感口感、安装步骤、清洁维护、礼赠理由或搭配建议；不要编造参数和认证。\n"
            "版式要求：可以采用主商品图加 3-5 个信息块、局部细节放大、图标式要点、轻量箭头、简洁分区或清单式排版；每个信息点必须短、清楚、可后期编辑，避免生成密集小字。\n"
            "视觉风格：干净、明亮、像优质小红书笔记整理图，颜色克制，留白充足，移动端浏览时主体和卖点都清楚。\n"
            "禁止：不要生成价格、促销倒计时、虚构销量、虚构用户反馈、医疗相关承诺、绝对化承诺、水印、二维码和不可辨认的小字。"
        ),
        "model": "gpt-image-2",
        "size": "1024x1365",
        "quality": "high",
        "preserve_subject": 1,
    },
]

LEGACY_DEFAULT_TEMPLATE_CONTENT = {
    ("电商主图", "main"): "生成一张电商商品主图，突出商品主体，画面干净高级，保留商品 Logo、文字、结构和材质细节，背景适合线上平台展示。",
    ("白底图", "white"): "生成标准电商白底图，商品居中完整展示，边缘自然清晰，保留原商品造型、Logo、文字和颜色，不添加多余装饰。",
    ("场景图", "scene"): "生成真实商业场景图，让商品自然融入使用环境，光影真实，质感高级，主体保持与参考商品一致。",
    ("详情页卖点图", "detail"): "生成商品详情页配图，突出核心卖点和材质细节，构图清晰，适合电商详情页使用，商品主体保持一致。",
}


class BusinessService:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or _database_url()
        self.engine = None
        self.Session = None
        self._init_error = ""
        self._list_cache = TTLCache[tuple[Any, ...], dict[str, Any]](ttl_seconds=3.0, max_items=128)
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            engine = create_engine(self.database_url, pool_pre_ping=True, pool_recycle=3600)
            Base.metadata.create_all(engine)
            self._ensure_indexes(engine)
            self.engine = engine
            self.Session = sessionmaker(bind=engine)
            self._init_error = ""
            self._ensure_default_templates()
        except Exception as exc:
            self.engine = None
            self.Session = None
            self._init_error = str(exc)

    def _session(self):
        if self.Session is None:
            self._init_engine()
        if self.Session is None:
            raise RuntimeError(f"business database unavailable: {self._init_error}")
        return self.Session()

    def _invalidate_list_cache(self) -> None:
        self._list_cache.clear()

    def _ensure_indexes(self, engine) -> None:
        if engine.dialect.name != "mysql":
            return
        with engine.begin() as connection:
            statements = [
                "CREATE INDEX idx_products_owner_updated ON business_products (owner_id, updated_at)",
                "CREATE INDEX idx_products_owner_sku ON business_products (owner_id, sku)",
                "CREATE INDEX idx_products_owner_status_updated ON business_products (owner_id, status, updated_at)",
                "CREATE INDEX idx_references_product ON business_product_references (product_id, created_at)",
                "CREATE INDEX idx_references_product_created_id ON business_product_references (product_id, created_at, id)",
                "CREATE INDEX idx_templates_owner_category ON business_prompt_templates (owner_id, category, enabled)",
                "CREATE INDEX idx_templates_owner_enabled_updated ON business_prompt_templates (owner_id, enabled, updated_at)",
                "CREATE INDEX idx_audit_owner_created ON business_audit_logs (owner_id, created_at)",
                "CREATE INDEX idx_audit_owner_created_id ON business_audit_logs (owner_id, created_at, id)",
            ]
            for statement in statements:
                try:
                    connection.execute(text(statement))
                except Exception:
                    pass

    def _ensure_default_templates(self) -> None:
        session = self._session()
        try:
            changed = False
            for item in DEFAULT_TEMPLATES:
                key = (_clean(item.get("name")), _clean(item.get("category")))
                row = (
                    session.query(PromptTemplateModel)
                    .filter(
                        PromptTemplateModel.owner_id == "local-admin",
                        PromptTemplateModel.created_by == "system",
                        PromptTemplateModel.name == key[0],
                        PromptTemplateModel.category == key[1],
                    )
                    .one_or_none()
                )
                if row is None:
                    session.add(PromptTemplateModel(owner_id="local-admin", created_by="system", **item))
                    changed = True
                    continue
                if _clean(row.content) != LEGACY_DEFAULT_TEMPLATE_CONTENT.get(key):
                    continue
                row.content = _clean(item.get("content"))
                row.model = _clean(item.get("model")) or None
                row.size = _clean(item.get("size")) or None
                row.quality = _clean(item.get("quality")) or None
                row.preserve_subject = 1 if item.get("preserve_subject") else 0
                row.enabled = 1 if item.get("enabled", True) else 0
                row.updated_at = _now()
                changed = True
            if changed:
                session.commit()
                self._invalidate_list_cache()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def _log(self, session, identity: dict[str, object], action: str, target_type: str, target_id: object, detail: str = "") -> None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        session.add(
            AuditLogModel(
                owner_id=owner_id,
                actor_id=owner_id,
                action=action,
                target_type=target_type,
                target_id=_clean(target_id),
                detail=detail,
            )
        )

    def record_audit_log(self, *, identity: dict[str, object], action: str, target_type: str, target_id: object, detail: str = "") -> None:
        session = self._session()
        try:
            self._log(session, identity, action, target_type, target_id, detail)
            session.commit()
            self._invalidate_list_cache()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_products(self, *, identity: dict[str, object], q: str = "", status: str = "active") -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        keyword = _clean(q)
        cache_key = ("products", owner_id, is_admin, keyword, status)

        def _build() -> dict[str, Any]:
            session = self._session()
            try:
                query = session.query(ProductModel)
                if not is_admin:
                    query = query.filter(ProductModel.owner_id == owner_id)
                if status:
                    query = query.filter(ProductModel.status == status)
                if keyword:
                    like = f"%{keyword}%"
                    query = query.filter(
                        or_(
                            ProductModel.name.like(like),
                            ProductModel.sku.like(like),
                            ProductModel.brand.like(like),
                            ProductModel.category.like(like),
                        )
                    )
                rows = query.order_by(desc(ProductModel.updated_at), desc(ProductModel.id)).limit(500).all()
                product_ids = [row.id for row in rows]
                references = {}
                if product_ids:
                    ref_rows = (
                        session.query(ProductReferenceModel)
                        .filter(ProductReferenceModel.product_id.in_(product_ids))
                        .order_by(desc(ProductReferenceModel.created_at), desc(ProductReferenceModel.id))
                        .all()
                    )
                    for ref in ref_rows:
                        references.setdefault(ref.product_id, []).append(self._public_reference(ref))
                return {
                    "items": [self._public_product(row, references.get(row.id, [])) for row in rows],
                    "total": len(rows),
                }
            finally:
                session.close()

        return self._list_cache.get_or_set(cache_key, _build)

    def get_product(self, *, identity: dict[str, object], product_id: int) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        try:
            query = session.query(ProductModel).filter(ProductModel.id == product_id)
            if not is_admin:
                query = query.filter(ProductModel.owner_id == owner_id)
            row = query.one_or_none()
            if row is None:
                return None
            refs = (
                session.query(ProductReferenceModel)
                .filter(ProductReferenceModel.product_id == product_id)
                .order_by(desc(ProductReferenceModel.created_at), desc(ProductReferenceModel.id))
                .all()
            )
            return self._public_product(row, [self._public_reference(ref) for ref in refs])
        finally:
            session.close()

    def create_product(self, *, identity: dict[str, object], data: dict[str, object]) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        name = _clean(data.get("name"))
        if not name:
            raise ValueError("商品名称不能为空")
        session = self._session()
        try:
            row = ProductModel(
                owner_id=owner_id,
                created_by=owner_id,
                name=name,
                sku=_clean(data.get("sku")) or None,
                brand=_clean(data.get("brand")) or None,
                category=_clean(data.get("category")) or None,
                selling_points=_clean(data.get("selling_points")) or None,
                notes=_clean(data.get("notes")) or None,
                status=_clean(data.get("status"), "active"),
            )
            session.add(row)
            session.flush()
            self._log(session, identity, "create", "product", row.id, row.name)
            session.commit()
            self._invalidate_list_cache()
            return self._public_product(row, [])
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_product(self, *, identity: dict[str, object], product_id: int, data: dict[str, object]) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        try:
            query = session.query(ProductModel).filter(ProductModel.id == product_id)
            if not is_admin:
                query = query.filter(ProductModel.owner_id == owner_id)
            row = query.one_or_none()
            if row is None:
                return None
            for field in ("name", "sku", "brand", "category", "selling_points", "notes", "status"):
                if field in data:
                    value = _clean(data.get(field))
                    if field == "name" and not value:
                        raise ValueError("商品名称不能为空")
                    if field == "status":
                        setattr(row, field, value or "active")
                    else:
                        setattr(row, field, value or None)
            row.updated_at = _now()
            self._log(session, identity, "update", "product", row.id, row.name)
            session.commit()
            self._invalidate_list_cache()
            return self.get_product(identity=identity, product_id=product_id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def upload_product_reference(
        self,
        *,
        identity: dict[str, object],
        product_id: int,
        payload: bytes,
        file_name: str,
        mime_type: str,
        base_url: str,
    ) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        try:
            query = session.query(ProductModel).filter(ProductModel.id == product_id)
            if not is_admin:
                query = query.filter(ProductModel.owner_id == owner_id)
            product = query.one_or_none()
            if product is None:
                return None
            stored = image_storage_service.save(payload, base_url)
            width, height = _image_dimensions(payload)
            row = ProductReferenceModel(
                product_id=product_id,
                owner_id=owner_id,
                file_name=_clean(file_name) or "reference.png",
                mime_type=_clean(mime_type) or "image/png",
                image_rel=stored.rel,
                image_url=stored.url,
                thumbnail_url=thumbnail_url(base_url, stored.rel),
                width=width,
                height=height,
                file_size=stored.size,
                storage=stored.storage,
            )
            product.updated_at = _now()
            session.add(row)
            session.flush()
            self._log(session, identity, "upload_reference", "product", product_id, row.image_rel)
            session.commit()
            self._invalidate_list_cache()
            return self._public_reference(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_templates(self, *, identity: dict[str, object], q: str = "", category: str = "", enabled_only: bool = True) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        keyword = _clean(q)
        cache_key = ("templates", owner_id, is_admin, keyword, category, enabled_only)

        def _build() -> dict[str, Any]:
            session = self._session()
            try:
                query = session.query(PromptTemplateModel)
                if not is_admin:
                    query = query.filter(or_(PromptTemplateModel.owner_id == owner_id, PromptTemplateModel.owner_id == "local-admin"))
                if enabled_only:
                    query = query.filter(PromptTemplateModel.enabled == 1)
                if category:
                    query = query.filter(PromptTemplateModel.category == category)
                if keyword:
                    like = f"%{keyword}%"
                    query = query.filter(or_(PromptTemplateModel.name.like(like), PromptTemplateModel.content.like(like)))
                rows = query.order_by(desc(PromptTemplateModel.updated_at), desc(PromptTemplateModel.id)).limit(500).all()
                return {"items": [self._public_template(row) for row in rows], "total": len(rows)}
            finally:
                session.close()

        return self._list_cache.get_or_set(cache_key, _build)

    def create_template(self, *, identity: dict[str, object], data: dict[str, object]) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        name = _clean(data.get("name"))
        content = _clean(data.get("content"))
        if not name:
            raise ValueError("模板名称不能为空")
        if not content:
            raise ValueError("模板内容不能为空")
        session = self._session()
        try:
            row = PromptTemplateModel(
                owner_id=owner_id,
                created_by=owner_id,
                name=name,
                category=_clean(data.get("category"), "main"),
                content=content,
                model=_clean(data.get("model")) or None,
                size=_clean(data.get("size")) or None,
                quality=_clean(data.get("quality")) or None,
                preserve_subject=1 if data.get("preserve_subject") else 0,
                enabled=1 if data.get("enabled", True) else 0,
            )
            session.add(row)
            session.flush()
            self._log(session, identity, "create", "template", row.id, row.name)
            session.commit()
            self._invalidate_list_cache()
            return self._public_template(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_template(self, *, identity: dict[str, object], template_id: int, data: dict[str, object]) -> dict[str, Any] | None:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        session = self._session()
        try:
            query = session.query(PromptTemplateModel).filter(PromptTemplateModel.id == template_id)
            if not is_admin:
                query = query.filter(PromptTemplateModel.owner_id == owner_id)
            row = query.one_or_none()
            if row is None:
                return None
            for field in ("name", "category", "content", "model", "size", "quality"):
                if field in data:
                    value = _clean(data.get(field))
                    if field in {"name", "content"} and not value:
                        raise ValueError("模板名称和内容不能为空")
                    if field == "category":
                        setattr(row, field, value or "main")
                    else:
                        setattr(row, field, value or None)
            if "preserve_subject" in data:
                row.preserve_subject = 1 if data.get("preserve_subject") else 0
            if "enabled" in data:
                row.enabled = 1 if data.get("enabled") else 0
            row.updated_at = _now()
            self._log(session, identity, "update", "template", row.id, row.name)
            session.commit()
            self._invalidate_list_cache()
            return self._public_template(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_audit_logs(self, *, identity: dict[str, object], limit: int = 100) -> dict[str, Any]:
        owner_id = _clean(identity.get("id")) or "local-admin"
        is_admin = _clean(identity.get("role")) == "admin"
        page_limit = max(1, min(500, limit))
        cache_key = ("audit_logs", owner_id, is_admin, page_limit)

        def _build() -> dict[str, Any]:
            session = self._session()
            try:
                query = session.query(AuditLogModel)
                if not is_admin:
                    query = query.filter(AuditLogModel.owner_id == owner_id)
                rows = query.order_by(desc(AuditLogModel.created_at), desc(AuditLogModel.id)).limit(page_limit).all()
                return {"items": [self._public_audit_log(row) for row in rows], "total": len(rows)}
            finally:
                session.close()

        return self._list_cache.get_or_set(cache_key, _build)

    @staticmethod
    def _public_product(row: ProductModel, references: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "sku": row.sku,
            "brand": row.brand,
            "category": row.category,
            "selling_points": row.selling_points,
            "notes": row.notes,
            "status": row.status,
            "references": references,
            "cover_image_url": references[0]["thumbnail_url"] if references else "",
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
        }

    @staticmethod
    def _public_reference(row: ProductReferenceModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "product_id": row.product_id,
            "file_name": row.file_name,
            "mime_type": row.mime_type,
            "image_rel": row.image_rel,
            "image_url": row.image_url,
            "thumbnail_url": row.thumbnail_url,
            "width": row.width,
            "height": row.height,
            "file_size": row.file_size,
            "storage": row.storage,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        }

    @staticmethod
    def _public_template(row: PromptTemplateModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "category": row.category,
            "content": row.content,
            "model": row.model,
            "size": row.size,
            "quality": row.quality,
            "preserve_subject": bool(row.preserve_subject),
            "enabled": bool(row.enabled),
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
        }

    @staticmethod
    def _public_audit_log(row: AuditLogModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "detail": row.detail,
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        }


business_service = BusinessService()
