"""Time-limited download links for ``read_attachment`` and binary fields.

A link carries an HMAC token over (model, record id, field, expiry), signed with
the database secret under its own scope, so a token of this route works nowhere
else. The token is a delegated-access bearer: the tool checked the caller's
access when it issued the link, and the route serves the bytes as ``sudo`` to
anyone holding it until it expires. Every refusal answers 404, so the route
reveals nothing about the record.
"""

import time

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request
from odoo.tools import consteq
from odoo.tools.misc import hmac

from . import utils

TOKEN_SCOPE = "mcp_download"


def download_token(record, field, expires):
    """Token for the download of ``field`` of ``record`` until ``expires``.

    :param expires: expiry as epoch seconds; the token embeds it in hex.
    :return: ``<hmac>o<hex expiry>``
    """
    record.ensure_one()
    timestamp = hex(int(expires))
    signature = hmac(
        record.env(su=True), TOKEN_SCOPE, (record._name, record.id, field, timestamp)
    )
    return f"{signature}o{timestamp}"


def verify_download_token(record, field, token):
    """Whether ``token`` grants the download of ``field`` of ``record`` now."""
    # consteq raises on a non-ASCII str: refuse it here, so the route still
    # answers 404 and does not reveal which records exist.
    if not token or not token.isascii():
        return False
    _signature, separator, timestamp = token.rpartition("o")
    if not separator:
        return False
    try:
        expires = int(timestamp, 16)
    except ValueError:
        return False
    return consteq(token, download_token(record, field, expires)) and (
        time.time() < expires
    )


class McpDownloadController(http.Controller):
    # auth="public": the link is opened without an Odoo login, the token is the
    # credential. csrf=False: GET only, no state change. sitemap=False: never
    # listed.
    @http.route(
        "/mcp/download/<string:model>/<int:res_id>/<string:field>",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        sitemap=False,
    )
    def download(self, model, res_id, field, access_token=None, **kwargs):
        """Stream the bytes of one binary field for a valid download token."""
        if not (access_token and utils.is_mcp_enabled()) or model not in request.env:
            raise NotFound()
        # sudo: delegated access. The tool checked the caller's read access
        # when it issued the token; the token binds model, id, field and expiry.
        # The checks that need no SQL run first: an abstract model has no
        # table, and the database is read only for a valid token.
        model_rs = request.env[model].sudo()
        model_field = model_rs._fields.get(field)
        if model_rs._abstract or model_field is None or model_field.type != "binary":
            raise NotFound()
        record = model_rs.browse(res_id)
        if not verify_download_token(record, field, access_token):
            raise NotFound()
        if not record.exists():
            raise NotFound()
        stream = request.env["ir.binary"]._get_stream_from(record, field)
        return stream.get_response(as_attachment=True)
