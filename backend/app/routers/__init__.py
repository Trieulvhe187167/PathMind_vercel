"""Every API router with its URL prefix (registered in main.create_app)."""

from . import (
    admin_accounts, admin_ai, admin_org, admin_overview, admin_roles, admin_system, admin_users, auth, billing,
    agents, chat, community, contributions, files, google_auth, kb, learning, me, social, study, tree,
)

ROUTERS = [
    (auth.router, "/api/auth", "auth"),
    (google_auth.router, "/api/auth/google", "auth"),
    (me.router, "/api/me", "me"),
    (files.router, "/api/files", "files"),
    (billing.router, "/api/billing", "billing"),
    (admin_ai.public_router, "/api/ai", "ai"),
    (tree.router, "/api/tree", "tree"),
    (contributions.router, "/api/contribute", "contribute"),
    (contributions.rnd_router, "/api/rnd", "rnd"),
    (chat.router, "/api/chat", "chat"),
    (chat.rnd_router, "/api/rnd", "rnd"),
    (kb.router, "/api/kb", "kb"),
    (learning.router, "/api", "learning"),
    (study.router, "/api", "learning"),
    (agents.router, "/api", "agents"),
    (social.router, "/api/social", "social"),
    (community.router, "/api/community", "community"),
    (admin_users.router, "/api/admin", "admin"),
    (admin_roles.router, "/api/admin", "admin"),
    (admin_overview.router, "/api/admin", "admin"),
    (admin_accounts.router, "/api/admin", "admin"),
    (admin_system.router, "/api/admin", "admin"),
    (admin_org.router, "/api/admin", "admin"),
    (admin_ai.router, "/api/admin/ai", "admin"),
    (billing.admin_router, "/api/admin/billing", "admin"),
]
