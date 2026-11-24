"""Shared ``mcp.log`` audit-write scaffolding.

Both the native ``/mcp`` audits (:mod:`~odoo.addons.mcp_server.controllers.mcp`)
and the legacy auth-failure audit
(:mod:`~odoo.addons.mcp_server.controllers.auth`) must persist an ``mcp.log`` row
even when the request transaction is later rolled back on error -- so in
PRODUCTION they open an INDEPENDENT registry cursor and explicitly commit it.

Under HttpCase tests that independent cursor is a REAL second transaction that
commits mid-request: it races the test's long-lived transaction (intermittent
"could not serialize access due to concurrent update") and leaks audit rows past
the per-test rollback. So under tests the row is written on the REQUEST cursor
instead -- still visible to the test's own audit-row assertions and rolled back
with it.

This one helper keeps that subtle, race-sensitive test/prod discipline in a
single place so the two call sites cannot drift apart.
"""

import logging

from odoo import api
from odoo.http import request
from odoo.tools import config

_logger = logging.getLogger(__name__)


def write_audit_row(uid, write_log, failure_message, *args):
    """Persist one ``mcp.log`` row, choosing the cursor by runtime mode.

    ``write_log`` is called with a sudo'd ``mcp.log`` recordset bound to ``uid``
    and performs the single log call that differs between callers. Any failure of
    the audit write itself is logged (``failure_message`` % ``args``) and
    swallowed -- auditing must never break the client response.

    Under ``test_enable`` the row is written on the request cursor; otherwise on
    a fresh, explicitly-committed independent cursor (see the module docstring).

    ``request.env`` is read INSIDE the try so an unbound request (e.g. a
    non-HttpCase unit test that reaches this via a partially mocked call site) is
    swallowed like any other audit-write failure -- auditing must never break the
    caller.
    """
    if config["test_enable"]:
        try:
            audit_log = api.Environment(request.env.cr, uid, {})[
                "mcp.log"
            ].sudo()  # sudo: audit write, not user data
            write_log(audit_log)
        except Exception:
            _logger.exception(failure_message, *args)
        return
    try:
        with request.env.registry.cursor() as cr:
            log_env = api.Environment(cr, uid, {})
            audit_log = log_env["mcp.log"].sudo()  # sudo: audit write, not user data
            write_log(audit_log)
            cr.commit()  # pylint: disable=invalid-commit
    except Exception:
        _logger.exception(failure_message, *args)
