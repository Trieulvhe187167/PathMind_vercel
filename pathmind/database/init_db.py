"""Database initialization: default subscription plans and model price list.

Identity (login, password, role) lives in the multi-user auth store
(``data/user/auth_users.json`` via ``pathmind.services.auth``). The relational
``users`` table is only a mirror used for foreign keys of subscriptions,
payments and usage logs; rows are created lazily by
``quota_service.ensure_db_user``. We therefore do NOT seed an admin account.

Plan limits are expressed in **credits** (cost-weighted tokens — see
``quota_service``). 1 credit ≈ 1 token of a model priced at the credit base
(default $0.25 per 1M blended tokens, i.e. the cheapest mainstream models).
The defaults keep the worst-case API cost of a paid plan at ≈55% of its price.
"""

import logging

from pathmind.database.connection import SessionLocal

logger = logging.getLogger(__name__)

MB = 1024 * 1024
GB = 1024 * MB

FREE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "deepseek-v4-flash",
    "gpt-6-luna",
]
PLUS_MODELS = FREE_MODELS + [
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "deepseek-v4-pro",
]
PRO_MODELS = FREE_MODELS + [
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "deepseek-v4-pro",
    "gpt-6-sol",
    "claude-haiku-4-5",
]
ENTERPRISE_MODELS = PRO_MODELS + [
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "claude-sonnet-4-6",
]

_LEGACY_DESCRIPTIONS = {
    "Dành cho học sinh làm quen với trợ lý AI và giải bài tập cơ bản.",
    "Dành cho người học chuyên sâu, ôn thi đại học và nghiên cứu khoa học.",
    "Dành cho nhóm học tập, lớp học hoặc dự án nghiên cứu quy mô lớn.",
}

DEFAULT_PLANS: list[dict] = [
    {
        "id": "free",
        "name": "Free Starter",
        "description": "For students getting started with an AI tutor and everyday homework.",
        "sort_order": 0,
        "price_monthly": 0.0,
        "price_yearly": 0.0,
        "max_tokens_per_day": 150_000,  # credits
        "max_tokens_per_month": 1_500_000,  # ≈ $0.38 API cost at most
        "max_storage_bytes": 200 * MB,
        "max_upload_file_size_bytes": 10 * MB,
        "allowed_models": FREE_MODELS,
        # Cheapest capable model: ×1.9 credits vs ×5.4 for gemini-3.6-flash,
        # so free users get ~3x more chats / quizzes from the same quota.
        "default_model": "deepseek-v4-flash",
        "allow_custom_api_key": False,
        "is_active": True,
    },
    {
        "id": "plus",
        "name": "Plus",
        "description": "For regular learners who want mastery paths, AI books and the community.",
        "sort_order": 5,
        "price_monthly": 4.99,
        "price_yearly": 49.0,
        "max_tokens_per_day": 1_000_000,
        "max_tokens_per_month": 11_000_000,  # ≈ $2.75 API cost at most (55% of price)
        "max_storage_bytes": 2 * GB,
        "max_upload_file_size_bytes": 25 * MB,
        "allowed_models": PLUS_MODELS,
        "allow_custom_api_key": False,
        "is_active": True,
    },
    {
        "id": "pro",
        "name": "Pro Learner",
        "description": "For dedicated learners, exam preparation and research.",
        "sort_order": 10,
        "price_monthly": 9.99,
        "price_yearly": 99.0,
        "max_tokens_per_day": 2_000_000,
        "max_tokens_per_month": 22_000_000,  # ≈ $5.50 API cost at most
        "max_storage_bytes": 10 * GB,
        "max_upload_file_size_bytes": 50 * MB,
        "allowed_models": PRO_MODELS,
        "allow_custom_api_key": True,
        "is_active": True,
    },
    {
        "id": "enterprise",
        "name": "Unlimited Campus",
        "description": "For study groups, classes and large research projects.",
        "sort_order": 20,
        "price_monthly": 29.99,
        "price_yearly": 299.0,
        "max_tokens_per_day": 6_000_000,
        "max_tokens_per_month": 66_000_000,  # ≈ $16.50 API cost at most
        "max_storage_bytes": 50 * GB,
        "max_upload_file_size_bytes": 100 * MB,
        "allowed_models": ENTERPRISE_MODELS,
        "allow_custom_api_key": True,
        "is_active": True,
    },
]

# Values shipped by the first release. A plan still holding exactly these was
# never edited by an admin, so it is safe to move it to the new defaults.
_LEGACY_LIMITS = {
    "free": (50_000, 1_500_000),
    "pro": (1_000_000, 30_000_000),
    "enterprise": (5_000_000, 150_000_000),
}
_LEGACY_MODEL_MARKERS = {"gpt-4o-mini", "gemini-1.5-flash", "claude-3-5-sonnet", "deepseek-chat"}

# USD per 1M tokens, list prices checked 2026-09 (DeepSeek: peak rate, to stay
# conservative). cached_input_per_m None → 25% of input price.
DEFAULT_MODEL_PRICES: list[dict] = [
    {"id": "*", "display_name": "Models not in the price list (safe default)", "provider": "", "input_per_m": 2.0, "cached_input_per_m": 0.2, "output_per_m": 10.0, "aliases": []},
    {"id": "gpt-6-luna", "display_name": "GPT-6 Luna", "provider": "openai", "input_per_m": 0.05, "cached_input_per_m": 0.005, "output_per_m": 0.25, "aliases": []},
    {"id": "gpt-6-sol", "display_name": "GPT-6 Sol", "provider": "openai", "input_per_m": 1.0, "cached_input_per_m": 0.1, "output_per_m": 5.0, "aliases": []},
    {"id": "gpt-6-astra", "display_name": "GPT-6 Astra", "provider": "openai", "input_per_m": 5.0, "cached_input_per_m": 0.5, "output_per_m": 25.0, "aliases": []},
    {"id": "deepseek-v4-flash", "display_name": "DeepSeek V4 Flash", "provider": "deepseek", "input_per_m": 0.3, "cached_input_per_m": 0.006, "output_per_m": 1.2, "aliases": ["deepseek-flash", "deepseek-chat"]},
    {"id": "deepseek-v4-pro", "display_name": "DeepSeek V4 Pro", "provider": "deepseek", "input_per_m": 1.32, "cached_input_per_m": 0.044, "output_per_m": 3.96, "aliases": ["deepseek-reasoner"]},
    {"id": "gemini-3.1-flash-lite", "display_name": "Gemini 3.1 Flash-Lite", "provider": "gemini", "input_per_m": 0.25, "cached_input_per_m": None, "output_per_m": 1.5, "aliases": ["gemini-flash-lite-latest"]},
    {"id": "gemini-3.5-flash-lite", "display_name": "Gemini 3.5 Flash-Lite", "provider": "gemini", "input_per_m": 0.3, "cached_input_per_m": None, "output_per_m": 2.5, "aliases": []},
    {"id": "gemini-3.6-flash", "display_name": "Gemini 3.6 Flash", "provider": "gemini", "input_per_m": 0.75, "cached_input_per_m": None, "output_per_m": 3.75, "aliases": [], "notes": "Giá tăng gấp đôi từ 01/01/2027"},
    {"id": "gemini-3.7-flash", "display_name": "Gemini 3.7 Flash", "provider": "gemini", "input_per_m": 0.75, "cached_input_per_m": None, "output_per_m": 3.75, "aliases": [], "notes": "Giá tăng gấp đôi từ 01/01/2027"},
    {"id": "gemini-3.8-flash", "display_name": "Gemini 3.8 Flash", "provider": "gemini", "input_per_m": 0.75, "cached_input_per_m": None, "output_per_m": 3.75, "aliases": ["gemini-flash-latest"], "notes": "Giá tăng gấp đôi từ 01/01/2027"},
    {"id": "gemini-3.5-flash", "display_name": "Gemini 3.5 Flash", "provider": "gemini", "input_per_m": 1.5, "cached_input_per_m": None, "output_per_m": 9.0, "aliases": []},
    {"id": "gemini-3.1-pro", "display_name": "Gemini 3.1 Pro", "provider": "gemini", "input_per_m": 2.0, "cached_input_per_m": None, "output_per_m": 12.0, "aliases": ["gemini-pro-latest"]},
    {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5", "provider": "anthropic", "input_per_m": 1.0, "cached_input_per_m": 0.1, "output_per_m": 5.0, "aliases": []},
    {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6", "provider": "anthropic", "input_per_m": 3.0, "cached_input_per_m": 0.3, "output_per_m": 15.0, "aliases": []},
    # Embedding models (input only). Billed when knowledge bases are indexed
    # and when a query is embedded for retrieval.
    {"id": "*embedding", "display_name": "Embedding models not in the price list", "provider": "", "input_per_m": 0.15, "cached_input_per_m": 0.15, "output_per_m": 0.0, "aliases": []},
    {"id": "text-embedding-3-small", "display_name": "OpenAI text-embedding-3-small", "provider": "openai", "input_per_m": 0.02, "cached_input_per_m": 0.02, "output_per_m": 0.0, "aliases": []},
    {"id": "text-embedding-3-large", "display_name": "OpenAI text-embedding-3-large", "provider": "openai", "input_per_m": 0.13, "cached_input_per_m": 0.13, "output_per_m": 0.0, "aliases": []},
    {"id": "text-embedding-ada-002", "display_name": "OpenAI text-embedding-ada-002", "provider": "openai", "input_per_m": 0.10, "cached_input_per_m": 0.10, "output_per_m": 0.0, "aliases": []},
    {"id": "gemini-embedding-001", "display_name": "Gemini Embedding", "provider": "gemini", "input_per_m": 0.15, "cached_input_per_m": 0.15, "output_per_m": 0.0, "aliases": ["gemini-embedding-exp"]},
]


def seed_initial_data() -> None:
    from pathmind.database.models import ModelPrice, SubscriptionPlan
    from pathmind.services.entitlements import default_entitlements

    db = SessionLocal()
    try:
        for p_data in DEFAULT_PLANS:
            existing = db.get(SubscriptionPlan, p_data["id"])
            if not existing:
                db.add(
                    SubscriptionPlan(
                        **p_data,
                        entitlements=default_entitlements(p_data["id"], p_data["price_monthly"]),
                    )
                )
                logger.info("Seeded subscription plan: %s", p_data["id"])
                continue
            if (
                existing.description is None
                or existing.description in _LEGACY_DESCRIPTIONS
            ):
                # Untouched first-release (Vietnamese) seed text → current default.
                existing.description = p_data["description"]
            if existing.sort_order is None:
                existing.sort_order = p_data["sort_order"]
            if existing.entitlements is None:
                existing.entitlements = default_entitlements(existing.id, existing.price_monthly)
                logger.info("Plan %s entitlements -> defaults", existing.id)
            elif (
                existing.id == "free"
                and isinstance(existing.entitlements, dict)
                and "allowance_period" not in existing.entitlements
                and existing.entitlements.get("mastery_per_month") == 0
                and existing.entitlements.get("books_per_month") == 0
            ):
                # First-release Free (no paths/books) -> one-time trial of each.
                existing.entitlements = {
                    **existing.entitlements,
                    "mastery_per_month": 1,
                    "books_per_month": 1,
                    "allowance_period": "lifetime",
                }
                logger.info("Plan free entitlements -> 1 path + 1 book trial")
            # NULL = never set: adopt the shipped default once. An admin who
            # clears it stores "" and is left alone.
            if existing.default_model is None and p_data.get("default_model"):
                existing.default_model = p_data["default_model"]
                logger.info("Plan %s default model -> %s", existing.id, p_data["default_model"])
            # One-time move of untouched first-release plans to credit limits.
            legacy = _LEGACY_LIMITS.get(existing.id)
            if legacy and (existing.max_tokens_per_day, existing.max_tokens_per_month) == legacy:
                existing.max_tokens_per_day = p_data["max_tokens_per_day"]
                existing.max_tokens_per_month = p_data["max_tokens_per_month"]
                logger.info("Migrated plan %s to credit-based limits", existing.id)
            if set(existing.allowed_models or []) & _LEGACY_MODEL_MARKERS:
                existing.allowed_models = p_data["allowed_models"]
                logger.info("Migrated plan %s to current model ids", existing.id)

        for price in DEFAULT_MODEL_PRICES:
            row = db.get(ModelPrice, price["id"])
            if not row:
                db.add(ModelPrice(is_active=True, **price))
            elif row.display_name == "Model chưa có trong bảng giá (mặc định an toàn)":
                row.display_name = price["display_name"]
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Error seeding database: %s", exc)
    finally:
        db.close()
