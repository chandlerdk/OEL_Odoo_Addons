"""Grant the MCP User access group to users with existing MCP activity.

Membership in ``mcp_server.group_mcp_user`` is required on every MCP surface.
Active internal users with prior MCP activity -- an ``mcp``-scope API key, an
OAuth token, or an MCP audit-log entry recording a completed operation --
receive the group automatically so their integrations keep working; for
everyone else access is an explicit admin decision.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# One query per activity source; each table is probed with to_regclass first.
_ACTIVITY_QUERIES = (
    (
        "res_users_apikeys",
        "SELECT DISTINCT user_id FROM res_users_apikeys WHERE scope = 'mcp'",
    ),
    (
        "mcp_oauth_token",
        "SELECT DISTINCT user_id FROM mcp_oauth_token WHERE user_id IS NOT NULL",
    ),
    (
        "mcp_log",
        # Allowlist, not a denylist: on the ``auth="none"`` XML-RPC proxy
        # ``user_id`` falls back to the raw client-supplied uid
        # (``controllers/api.py`` ``_identify_user``), so an unauthenticated
        # caller can plant rate_limit / permission_denied / error rows against
        # any uid and have this migration grant it the group. Only the event
        # types below are written after a verified credential. The rows of the
        # version 1.2 XML-RPC proxy are left out as well: its ``security.check``
        # patch returned False instead of raising for the password
        # ``__mcp_api_key__``, so core ran the call, and logged model_access,
        # for any uid. The 1.2 REST routes checked the API key first.
        "SELECT DISTINCT user_id FROM mcp_log "
        "WHERE user_id IS NOT NULL AND event_type IN "
        "('model_access', 'write_operation', 'resource_retrieval') "
        "AND COALESCE(endpoint, '') NOT LIKE '/mcp/xmlrpc/%'",
    ),
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Version 1.2 put both groups in the hidden category, and the XML update
    # keeps a field that the XML no longer sets. The user form shows a group of
    # that category in debug mode only.
    hidden = env.ref("base.module_category_hidden", raise_if_not_found=False)
    for xmlid in ("mcp_server.group_mcp_user", "mcp_server.group_mcp_admin"):
        group = env.ref(xmlid, raise_if_not_found=False)
        if group is not None and hidden and group.category_id == hidden:
            group.category_id = False

    group = env.ref("mcp_server.group_mcp_user", raise_if_not_found=False)
    if group is None:
        return

    user_ids = set()
    for table, query in _ACTIVITY_QUERIES:
        cr.execute("SELECT to_regclass(%s)", (table,))
        if cr.fetchone()[0] is None:
            continue
        cr.execute(query)
        user_ids.update(row[0] for row in cr.fetchall())

    if not user_ids:
        _logger.info(
            "No prior MCP activity found: the MCP User access group was granted "
            "to nobody. Existing integrations will be refused with 403 until an "
            "administrator assigns the group."
        )
        return

    users = (
        env["res.users"]
        .browse(sorted(user_ids))
        .exists()
        .filtered(lambda user: user.active and not user.share)
    )
    if not users:
        _logger.info(
            "Prior MCP activity resolved to no active internal user: the MCP "
            "User access group was granted to nobody."
        )
        return

    # User-side write: res.users.write invalidates and signals the full
    # registry cache, so warm HTTP workers see the grant on their next request.
    users.write({"groups_id": [(4, group.id)]})
    _logger.info(
        "Granted the MCP User access group to %s user(s): %s",
        len(users),
        ", ".join(users.mapped("login")),
    )
