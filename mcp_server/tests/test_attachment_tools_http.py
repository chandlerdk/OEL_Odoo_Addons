"""End-to-end tests for the attachment tools over ``POST /mcp``.

Wire-level coverage that the TransactionCase suite
(``test_attachment_tools.py``) cannot reach:

* ``tools/list`` advertises ``read_attachment`` / ``list_record_attachments``
  / ``upload_attachment`` with the right ``readOnlyHint`` and ``title``.
* Discovery cross-references: the ``binary_note`` next to swapped ``odoo://``
  URIs in ``get_record`` and ``search_records`` (sibling key, exact URI values
  unchanged), both resource-template listings, and the usage guidance.
* ``tools/call read_attachment`` results for ``auto`` / ``blob`` / ``link``,
  including redeeming the link with a fresh, sessionless HTTP client (and an
  expired one being refused); gated model -> ``isError``.
* ``tools/call upload_attachment``; an ``mcp:read`` OAuth session is refused
  the upload but may still request a download link; audit rows.

Mirrors the HttpCase + ``_generate("rpc", ...)`` key-minting pattern of
``test_mcp_read_tools.py``; ``mcp_server.enabled`` is set in ``setUp`` (a
fresh CI database has it off).
"""

import base64
import json
import re
import secrets
import time
from urllib.parse import parse_qsl, urlsplit

import requests

from odoo.tests import common, tagged
from odoo.tests.common import TEST_CURSOR_COOKIE_NAME
from odoo.tools.misc import hmac

from ..controllers import mcp, oauth_server, rate_limiting, utils
from ..controllers.download import download_token
from .test_helpers import (
    UrlOpenCompatMixin,
    create_test_user,
    grant_mcp_access,
    users_groups_field,
)
from .test_oauth import _code_challenge

_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4"
    "nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
_PNG_1X1 = base64.b64decode(_PNG_1X1_B64)

ATTACHMENT_TOOLS = {
    "read_attachment": True,
    "list_record_attachments": True,
    "upload_attachment": False,
}


@tagged("much_unit", "post_install", "-at_install")
class TestAttachmentToolsHttp(UrlOpenCompatMixin, common.HttpCase):
    """Attachment tools on the native ``/mcp`` endpoint."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        rate_limiting._api_limiter.clear()
        oauth_server._dcr_limiter.clear()
        mcp._audit_write_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.login = f"mcp_att_http_{unique_id}"
        self.password = "att_pw"  # nosec B105 - test fixture credential
        groups_field = users_groups_field(self.env)
        self.user = create_test_user(
            self.env,
            "MCP Attachment HTTP User",
            self.login,
            password=self.password,
            email=f"mcp_att_http_{unique_id}@example.com",
            **{
                groups_field: [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("base.group_partner_manager").id,
                        ],
                    )
                ]
            },
        )
        grant_mcp_access(self.user)
        self.api_key = self.env(user=self.user)["res.users.apikeys"]._generate(
            "rpc", "Attachment Tools Key"
        )

        self.partner = self.env["res.partner"].create(
            {"name": f"Attachment HTTP Partner {unique_id}", "image_1920": _PNG_1X1_B64}
        )
        self.text_attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "note.txt",
                    "mimetype": "text/plain",
                    "raw": b"hello over http",
                    "res_model": "res.partner",
                    "res_id": self.partner.id,
                }
            )
        )

        self._enable_model("base.model_res_partner", allow_read=True, allow_write=True)
        self._disable_model("base.model_ir_attachment")
        self._disable_model("base.model_res_users")

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("mcp_server.enable_logging", "True")
        params.set_param("mcp_server.enable_oauth", "True")
        params.set_param("web.base.url", self.base_url())
        utils.clear_mcp_caches()

    # ------------------------------------------------------------------
    # Fixture helpers
    # ------------------------------------------------------------------
    def _enable_model(self, model_xmlid, **perms):
        model_id = self.env.ref(model_xmlid).id
        record = (
            self.env["mcp.enabled.model"]
            .sudo()
            .with_context(active_test=False)
            .search([("model_id", "=", model_id)], limit=1)
        )
        vals = {
            "active": True,
            "allow_read": False,
            "allow_create": False,
            "allow_write": False,
            "allow_unlink": False,
            **perms,
        }
        if record:
            record.write(vals)
        else:
            record = (
                self.env["mcp.enabled.model"]
                .sudo()
                .create({"model_id": model_id, **vals})
            )
        utils.clear_mcp_caches()
        return record

    def _disable_model(self, model_xmlid):
        model_id = self.env.ref(model_xmlid).id
        self.env["mcp.enabled.model"].sudo().search(
            [("model_id", "=", model_id)]
        ).unlink()
        utils.clear_mcp_caches()

    # ------------------------------------------------------------------
    # RPC helpers
    # ------------------------------------------------------------------
    def _rpc(self, method, params=None, token=None):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token or self.api_key}",
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        response = self.url_open("/mcp", data=json.dumps(body), headers=headers)
        self.assertEqual(response.status_code, 200, response.text[:500])
        payload = response.json()
        self.assertNotIn("error", payload, msg=payload.get("error"))
        return payload["result"]

    def _call_tool(self, name, arguments=None, token=None):
        return self._rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}, token=token
        )

    def _tools(self, token=None):
        return {
            tool["name"]: tool for tool in self._rpc("tools/list", token=token)["tools"]
        }

    def _anonymous_get(self, url):
        """GET ``url`` with a cookie-less client (plus the test-cursor key)."""
        self.cr.flush()
        self.cr.clear()
        with self.allow_requests():
            anonymous = requests.Session()
            anonymous.cookies.set(TEST_CURSOR_COOKIE_NAME, self.http_request_key)
            return anonymous.get(url, timeout=10)

    def _last_log(self, tool_name):
        return (
            self.env["mcp.log"]
            .sudo()
            .search([("tool_name", "=", tool_name)], order="id desc", limit=1)
        )

    # ------------------------------------------------------------------
    # tools/list + discovery cross-references (plan Task 4)
    # ------------------------------------------------------------------
    def test_tools_list_advertises_attachment_tools(self):
        tools = self._tools()
        for name, read_only in ATTACHMENT_TOOLS.items():
            self.assertIn(name, tools, msg=list(tools))
            self.assertIs(tools[name]["annotations"]["readOnlyHint"], read_only, name)
            self.assertTrue(tools[name].get("title"), name)
        read_schema = tools["read_attachment"]["inputSchema"]
        self.assertEqual(
            set(read_schema["properties"]), {"uri", "attachment_id", "format"}
        )
        self.assertEqual(
            read_schema["properties"]["format"]["enum"], ["auto", "link", "blob"]
        )
        self.assertEqual(
            set(tools["list_record_attachments"]["inputSchema"]["required"]),
            {"model", "record_id"},
        )
        # Live limit interpolation applies to the new paging tool too.
        limit_desc = tools["list_record_attachments"]["inputSchema"]["properties"][
            "limit"
        ]["description"]
        self.assertNotIn("%(default)s", limit_desc)
        self.assertEqual(
            set(tools["upload_attachment"]["inputSchema"]["required"]),
            {"name", "data"},
        )

    def test_binary_note_in_get_record_and_search_records(self):
        expected_uri = f"odoo://record/res.partner/{self.partner.id}/image_1920"

        get_result = self._call_tool(
            "get_record",
            {
                "model": "res.partner",
                "record_id": self.partner.id,
                "fields": ["name", "image_1920"],
            },
        )
        self.assertFalse(get_result["isError"], msg=get_result)
        structured = get_result["structuredContent"]
        self.assertIn("read_attachment", structured["binary_note"])
        self.assertEqual(structured["record"]["image_1920"], expected_uri)
        self.assertIn("read_attachment", get_result["content"][0]["text"])

        # Smart-field selection keeps its own ``metadata.note`` untouched (the
        # binary hint lives on a sibling key and only appears when a binary
        # field was actually swapped).
        smart = self._call_tool(
            "get_record", {"model": "res.partner", "record_id": self.partner.id}
        )["structuredContent"]
        self.assertIn("Smart field selection", smart["metadata"]["note"])
        self.assertEqual(
            "binary_note" in smart, "image_1920" in smart["record"], msg=smart
        )

        search_result = self._call_tool(
            "search_records",
            {
                "model": "res.partner",
                "domain": [["id", "=", self.partner.id]],
                "fields": ["name", "image_1920"],
            },
        )
        self.assertFalse(search_result["isError"], msg=search_result)
        structured = search_result["structuredContent"]
        self.assertIn("read_attachment", structured["binary_note"])
        self.assertEqual(structured["records"][0]["image_1920"], expected_uri)
        self.assertIn("read_attachment", search_result["content"][0]["text"])

    def test_no_binary_note_without_binary_fields(self):
        result = self._call_tool(
            "get_record",
            {"model": "res.partner", "record_id": self.partner.id, "fields": ["name"]},
        )
        self.assertNotIn("binary_note", result["structuredContent"])
        self.assertNotIn("read_attachment", result["content"][0]["text"])

    def test_template_listings_mention_read_attachment(self):
        listing = self._rpc("resources/templates/list")
        for template in listing["resourceTemplates"]:
            self.assertIn("read_attachment", template["description"])
        tool = self._call_tool("list_resource_templates")
        for template in tool["structuredContent"]["templates"]:
            self.assertIn("read_attachment", template["description"])
        self.assertIn("read_attachment", tool["structuredContent"]["note"])

    def test_usage_guidance_names_attachment_tools(self):
        context = self._call_tool("get_current_context")["content"][0]["text"]
        for name in ATTACHMENT_TOOLS:
            self.assertIn(name, context)
        self.assertNotIn("resources/read", context)

    # ------------------------------------------------------------------
    # read_attachment over the wire (plan Task 5)
    # ------------------------------------------------------------------
    def test_read_attachment_auto_text(self):
        result = self._call_tool(
            "read_attachment", {"attachment_id": self.text_attachment.id}
        )
        self.assertFalse(result["isError"], msg=result)
        self.assertEqual(
            result["content"], [{"type": "text", "text": "hello over http"}]
        )
        self.assertEqual(result["structuredContent"]["format_used"], "text")
        log = self._last_log("read_attachment")
        self.assertEqual(log.model_name, "ir.attachment")
        self.assertIn(str(self.text_attachment.id), log.record_ids or "")

    def test_read_attachment_blob_embedded_resource(self):
        uri = f"odoo://record/res.partner/{self.partner.id}/image_1920"
        result = self._call_tool("read_attachment", {"uri": uri, "format": "blob"})
        self.assertFalse(result["isError"], msg=result)
        resource = result["content"][1]
        self.assertEqual(resource["type"], "resource")
        self.assertEqual(resource["resource"]["uri"], uri)
        self.assertEqual(resource["resource"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(resource["resource"]["blob"]), _PNG_1X1)
        log = self._last_log("read_attachment")
        self.assertEqual(log.model_name, "res.partner")

    def test_read_attachment_link_downloads_without_session(self):
        result = self._call_tool(
            "read_attachment",
            {"attachment_id": self.text_attachment.id, "format": "link"},
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.assertEqual(structured["format_used"], "link")
        self.assertTrue(structured["download_url"].startswith(self.base_url()))

        # Fresh client: no session cookie, no bearer -- the token alone must
        # work. Only the HttpCase per-request test key travels (test plumbing
        # so the server binds the test transaction; it carries no session).
        response = self._anonymous_get(structured["download_url"])
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertEqual(response.content, b"hello over http")

        disposition = response.headers.get("Content-Disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn(self.text_attachment.name, disposition)
        self.assertTrue(response.headers["Content-Type"].startswith("text/plain"))

        # An expired token is refused.
        expired = download_token(self.text_attachment, "raw", int(time.time()) - 10)
        response = self._anonymous_get(
            f"{self.base_url()}/mcp/download/ir.attachment/{self.text_attachment.id}"
            f"/raw?access_token={expired}"
        )
        self.assertEqual(response.status_code, 404)

    def test_download_route_refuses_every_other_token(self):
        """Every refusal of the download route is a 404 that reveals nothing."""
        att = self.text_attachment
        base = f"{self.base_url()}/mcp/download"
        good = download_token(att, "raw", int(time.time()) + 600)
        signature, _sep, timestamp = good.rpartition("o")
        tampered = ("0" if signature[0] != "0" else "1") + signature[1:]
        exp = hex(int(time.time()) + 600)
        other_scope = hmac(
            self.env(su=True), "binary", ("ir.attachment", att.id, "raw", exp)
        )

        def url(path, token):
            return f"{base}/{path}?access_token={token}"

        own = f"ir.attachment/{att.id}/raw"
        missing = att.copy()
        missing.unlink()
        cases = {
            "tampered token": url(own, f"{tampered}o{timestamp}"),
            "bad hex expiry": url(own, f"{signature}ozz"),
            "no token": f"{base}/{own}",
            "other record": url(f"ir.attachment/{att.id + 100000}/raw", good),
            "other field": url(f"ir.attachment/{att.id}/datas", good),
            "not a binary field": url(f"ir.attachment/{att.id}/name", good),
            "unknown model": url(f"no.such.model/{att.id}/raw", good),
            # No SQL runs before the token check: an abstract model has no table.
            "abstract model": url("mail.thread/1/x", good),
            "deleted record": url(
                f"ir.attachment/{missing.id}/raw",
                download_token(missing, "raw", int(time.time()) + 600),
            ),
            "token of scope binary": url(own, f"{other_scope}o{exp}"),
            "non-ASCII token": url(own, "\u00e9o1"),
        }
        for label, link in cases.items():
            with self.subTest(label):
                self.assertEqual(self._anonymous_get(link).status_code, 404)
        ok = url(own, good)
        self.assertEqual(self._anonymous_get(ok).status_code, 200)

        self.env["ir.config_parameter"].sudo().set_param("mcp_server.enabled", "False")
        utils.clear_mcp_caches()
        self.assertEqual(self._anonymous_get(ok).status_code, 404)

    def test_download_route_serves_a_record_field(self):
        """A link for a binary field of a record downloads with the record name."""
        result = self._call_tool(
            "read_attachment",
            {
                "uri": f"odoo://record/res.partner/{self.partner.id}/image_1920",
                "format": "link",
            },
        )
        self.assertFalse(result["isError"], msg=result)
        response = self._anonymous_get(result["structuredContent"]["download_url"])
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertEqual(response.content, _PNG_1X1)
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))

    def test_read_attachment_gated_model_is_iserror(self):
        gated = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "gated.txt",
                    "mimetype": "text/plain",
                    "raw": b"gated",
                    "public": True,
                    "res_model": "res.users",
                    "res_id": self.user.id,
                }
            )
        )
        result = self._call_tool("read_attachment", {"attachment_id": gated.id})
        self.assertTrue(result["isError"], msg=result)
        self.assertNotIn("Traceback", result["content"][0]["text"])
        self.env.invalidate_all()
        denied = (
            self.env["mcp.log"]
            .sudo()
            .search(
                [
                    ("event_type", "=", "permission_denied"),
                    ("model_name", "=", "ir.attachment"),
                    ("operation", "=", "read"),
                    ("user_id", "=", self.user.id),
                ]
            )
        )
        self.assertEqual(len(denied), 1)

    def test_read_attachment_bad_input_is_iserror(self):
        result = self._call_tool("read_attachment", {})
        self.assertTrue(result["isError"], msg=result)
        self.assertIn("exactly one", result["content"][0]["text"])

    # ------------------------------------------------------------------
    # upload_attachment + OAuth read-only scope (plan Task 5)
    # ------------------------------------------------------------------
    def test_upload_attachment_over_wire(self):
        result = self._call_tool(
            "upload_attachment",
            {
                "name": "memo.txt",
                "data": base64.b64encode(b"memo body").decode("ascii"),
                "model": "res.partner",
                "record_id": self.partner.id,
            },
        )
        self.assertFalse(result["isError"], msg=result)
        structured = result["structuredContent"]
        self.env.invalidate_all()
        attachment = (
            self.env["ir.attachment"].sudo().browse(structured["attachment_id"])
        )
        self.assertEqual(attachment.raw, b"memo body")
        self.assertEqual(attachment.res_id, self.partner.id)
        log = self._last_log("upload_attachment")
        self.assertEqual(log.model_name, "res.partner")
        self.assertEqual(log.operation, "create")

        # Round trip over the wire.
        read = self._call_tool("read_attachment", {"uri": structured["uri"]})
        self.assertEqual(read["content"][0]["text"], "memo body")

    def _readonly_oauth_token(self):
        """Run the OAuth flow with write consent withheld -> ``mcp:read``."""
        register = self.url_open(
            "/mcp/oauth/register",
            json={
                "client_name": "Attachment RO Client",
                "redirect_uris": ["http://127.0.0.1:8765/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "mcp mcp:read mcp:write",
            },
        )
        self.assertIn(register.status_code, (200, 201), register.text[:500])
        client_id = register.json()["client_id"]
        self.authenticate(self.login, self.password)

        verifier = secrets.token_urlsafe(48)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://127.0.0.1:8765/callback",
            "scope": "mcp",
            "state": "state-att",
            "resource": self.base_url() + "/mcp",
            "code_challenge": _code_challenge(verifier),
            "code_challenge_method": "S256",
        }
        page = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(page.status_code, 200, page.text[:500])
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
        self.assertIsNotNone(match)
        post_data = {**params, "csrf_token": match.group(1), "decision": "allow"}
        redirect = self.url_open(
            "/mcp/oauth/authorize", data=post_data, allow_redirects=False
        )
        self.assertEqual(redirect.status_code, 302, redirect.text[:500])
        code = dict(parse_qsl(urlsplit(redirect.headers["Location"]).query))["code"]
        token_resp = self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://127.0.0.1:8765/callback",
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": self.base_url() + "/mcp",
            },
            allow_redirects=False,
        )
        self.assertEqual(token_resp.status_code, 200, token_resp.text[:500])
        return token_resp.json()["access_token"]

    def test_readonly_scope_blocks_upload_but_allows_link(self):
        token = self._readonly_oauth_token()
        names = set(self._tools(token=token))
        self.assertNotIn("upload_attachment", names)
        self.assertIn("read_attachment", names)
        self.assertIn("list_record_attachments", names)

        denied = self._call_tool(
            "upload_attachment",
            {"name": "x.txt", "data": base64.b64encode(b"x").decode("ascii")},
            token=token,
        )
        self.assertTrue(denied["isError"], msg=denied)
        self.assertIn("read-only", denied["content"][0]["text"].lower())

        link = self._call_tool(
            "read_attachment",
            {"attachment_id": self.text_attachment.id, "format": "link"},
            token=token,
        )
        self.assertFalse(link["isError"], msg=link)
        self.assertIn("download_path", link["structuredContent"])

        listing = self._call_tool(
            "list_record_attachments",
            {"model": "res.partner", "record_id": self.partner.id},
            token=token,
        )
        self.assertFalse(listing["isError"], msg=listing)
        self.assertEqual(listing["structuredContent"]["total"], 1)
