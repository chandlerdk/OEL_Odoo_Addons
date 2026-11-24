"""Unit tests for the attachment MCP tools (``read_attachment`` & co).

TransactionCase coverage of the tool methods on ``mcp.mixin``, invoked as the
MCP user so the MCP allow-list gate *and* Odoo's own attachment ACL bind:

* ``read_attachment`` ``format='auto'`` ladder: text inline, image block,
  PDF/Office extracted text (stored ``index_content`` first, then
  ``_index``), and the download-link fallbacks (over-cap, no extractable
  text, parser failure, other mimetypes, ``type='url'``).
* ``format='link'``: no attachment / business-data mutation, no payload read,
  URL shape for both target kinds, TTL config parsing and clamping.
* ``format='blob'``: embedded-resource block, raw-byte cap.
* Gating: parent-model / ``ir.attachment`` enablement, record-field URIs
  always needing the parent model, input validation, ACL denial.
* Audit attribution helpers on the controller (target model / ids).

The wire format over ``POST /mcp`` is covered by
``test_attachment_tools_http.py``.
"""

import base64
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, MissingError, UserError
from odoo.tests import common, tagged

from ..controllers import utils
from ..controllers.download import verify_download_token
from ..controllers.mcp import MCPController
from ..models import mcp_mixin
from ..models.mcp_tools_read import DEFAULT_LINK_TTL_HOURS, MAX_LINK_TTL_HOURS
from .test_helpers import create_test_user, grant_mcp_access, users_groups_field

# Valid 1x1 RGB PNG (base64 for the Image field, raw bytes for attachments).
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4"
    "nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
_PNG_1X1 = base64.b64decode(_PNG_1X1_B64)
# Minimal PDF-looking payload (the parser is patched in the tests).
_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"

_MIXIN_LOGGER = "odoo.addons.mcp_server.models.mcp_mixin"


@tagged("much_unit", "post_install", "-at_install")
class TestAttachmentToolsBase(common.TransactionCase):
    """Shared fixtures: an MCP user, an enabled parent model, attachments."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        unique_id = str(int(time.time() * 1000))[-6:]
        cls.mcp_user = create_test_user(
            cls.env,
            "MCP Attachment User",
            f"mcp_attachment_user_{unique_id}",
            email=f"mcp_attachment_{unique_id}@example.com",
        )
        grant_mcp_access(cls.mcp_user)
        # Partner manager: plain internal users may only read res.partner, and
        # the upload tests need a writable (non-admin) parent record.
        cls.mcp_user.write(
            {
                users_groups_field(cls.env): [
                    (4, cls.env.ref("base.group_partner_manager").id)
                ]
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": f"Attachment Partner {unique_id}", "image_1920": _PNG_1X1_B64}
        )
        cls.mixin = cls.env(user=cls.mcp_user)["mcp.mixin"]

    def setUp(self):
        super().setUp()
        # The gate helpers read the master switch off ``request.env``; bind a
        # request-like object to the test env (no HTTP layer in a
        # TransactionCase).
        mock_request = MagicMock()
        mock_request.env = self.env
        patcher = patch(
            "odoo.addons.mcp_server.controllers.utils.request", mock_request
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("web.base.url", "https://odoo.example.com")
        self._disable_model("base.model_ir_attachment")
        self._disable_model("base.model_res_users")
        self._enable_model("base.model_res_partner", allow_read=True)

    # -- fixtures --------------------------------------------------------
    def _enable_model(self, model_xmlid, **perms):
        model_id = self.env.ref(model_xmlid).id
        record = (
            self.env["mcp.enabled.model"]
            .sudo()
            .with_context(active_test=False)
            .search([("model_id", "=", model_id)], limit=1)
        )
        # Reset every operation flag: the dev DB may carry a pre-existing row
        # with broader permissions than the test intends.
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

    def _attachment(self, name, raw, mimetype, **vals):
        """Create an attachment on the partner (sudo, image post-processing off)."""
        return (
            self.env["ir.attachment"]
            .sudo()
            .with_context(image_no_postprocess=True)
            .create(
                {
                    "name": name,
                    "raw": raw,
                    "mimetype": mimetype,
                    "res_model": "res.partner",
                    "res_id": self.partner.id,
                    **vals,
                }
            )
        )

    def _fake_file_size(self, attachment, size):
        """Stamp a fake ``file_size`` (core ``write`` strips the field)."""
        self.env.cr.execute(
            "UPDATE ir_attachment SET file_size = %s WHERE id = %s",
            (size, attachment.id),
        )
        attachment.invalidate_recordset(["file_size"])

    def _forbid_payload_read(self):
        """Patch the payload loader so any byte read fails the test."""
        return patch.object(
            type(self.mixin),
            "_load_target_bytes",
            side_effect=AssertionError("payload must not be loaded"),
        )

    def _patch_index(self, **kwargs):
        """Patch ``ir.attachment._index`` (return_value / side_effect)."""
        return patch.object(type(self.env["ir.attachment"]), "_index", **kwargs)

    @staticmethod
    def _token_expiry(download_path):
        """Epoch expiry embedded in the link's access token (``...o<hex>``)."""
        token = download_path.split("access_token=", 1)[1].split("&", 1)[0]
        return int(token.rsplit("o", 1)[1], 16)


class TestReadAttachmentAuto(TestAttachmentToolsBase):
    """``read_attachment`` with the default ``format='auto'``."""

    def test_text_attachment_inline(self):
        att = self._attachment("note.txt", b"hello mcp", "text/plain")
        result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["content"], [{"type": "text", "text": "hello mcp"}])
        structured = result["structuredContent"]
        self.assertEqual(structured["format_used"], "text")
        self.assertEqual(structured["attachment_id"], att.id)
        self.assertEqual(structured["mimetype"], "text/plain")
        self.assertEqual(structured["file_size"], 9)
        self.assertEqual(structured["uri"], f"odoo://attachment/{att.id}")
        self.assertFalse(structured["truncated"])
        # Clients that render only structuredContent must still see the text.
        self.assertEqual(structured["text"], "hello mcp")

    def test_text_uri_input(self):
        att = self._attachment("note.txt", b"via uri", "text/plain")
        result = self.mixin.read_attachment(uri=f"odoo://attachment/{att.id}")
        self.assertEqual(result["content"][0]["text"], "via uri")

    def test_image_returns_image_block(self):
        att = self._attachment("pixel.png", _PNG_1X1, "image/png")
        result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["structuredContent"]["format_used"], "image")
        self.assertEqual(len(result["content"]), 2)
        self.assertEqual(result["content"][0]["type"], "text")
        image = result["content"][1]
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(image["data"]), _PNG_1X1)

    def test_svg_is_text_not_image(self):
        att = self._attachment(
            "icon.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"
        )
        result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["structuredContent"]["format_used"], "text")
        self.assertIn("<svg", result["content"][0]["text"])

    def test_oversized_image_falls_back_to_link_without_payload_read(self):
        big = b"\x89PNG" + b"\0" * (mcp_mixin.MAX_INLINE_BLOB_BYTES + 1)
        att = self._attachment("big.png", big, "image/png")
        with self._forbid_payload_read():
            result = self.mixin.read_attachment(attachment_id=att.id)
        structured = result["structuredContent"]
        self.assertEqual(structured["format_used"], "link")
        self.assertIn("download_path", structured)
        self.assertIn("too large", result["content"][0]["text"])

    def test_record_field_uri_image(self):
        uri = f"odoo://record/res.partner/{self.partner.id}/image_1920"
        result = self.mixin.read_attachment(uri=uri)
        structured = result["structuredContent"]
        self.assertEqual(structured["format_used"], "image")
        self.assertEqual(structured["model"], "res.partner")
        self.assertEqual(structured["record_id"], self.partner.id)
        self.assertEqual(structured["field"], "image_1920")
        self.assertNotIn("attachment_id", structured)
        self.assertEqual(result["content"][1]["type"], "image")

    def test_plain_binary_column_is_sniffed(self):
        """A non-attachment binary field has no metadata until its bytes load."""
        self._enable_model("base.model_res_company", allow_read=True)
        company = self.env.company
        company.sudo().write({"logo_web": base64.b64encode(_PNG_1X1)})
        uri = f"odoo://record/res.company/{company.id}/logo_web"
        result = self.mixin.read_attachment(uri=uri)
        structured = result["structuredContent"]
        self.assertEqual(structured["format_used"], "image")
        self.assertEqual(structured["mimetype"], "image/png")
        self.assertEqual(structured["file_size"], len(_PNG_1X1))
        self.assertIsNone(structured["name"])
        # ``link`` never reads the column: metadata stays unknown.
        with self._forbid_payload_read():
            link = self.mixin.read_attachment(uri=uri, format="link")
        self.assertIsNone(link["structuredContent"]["mimetype"])
        self.assertIn(
            f"/mcp/download/res.company/{company.id}/logo_web?access_token=",
            link["structuredContent"]["download_path"],
        )

    def test_pdf_extracted_text_via_index(self):
        att = self._attachment("invoice.pdf", _PDF_BYTES, "application/pdf")
        att.sudo().write({"index_content": False})
        with self._patch_index(return_value="Invoice total 42"):
            result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["content"][0]["text"], "Invoice total 42")
        self.assertEqual(result["structuredContent"]["format_used"], "extracted_text")
        self.assertEqual(result["structuredContent"]["text"], "Invoice total 42")

    def test_pdf_without_extractor_returns_link_and_notice(self):
        att = self._attachment("invoice.pdf", _PDF_BYTES, "application/pdf")
        att.sudo().write({"index_content": False})
        # Base ``_index`` yields nothing for a non-text mimetype.
        with self._patch_index(return_value=None):
            result = self.mixin.read_attachment(attachment_id=att.id)
        structured = result["structuredContent"]
        self.assertEqual(structured["format_used"], "link")
        self.assertIn("attachment_indexation", result["content"][0]["text"])
        self.assertIn("download_path", structured)

    def test_pdf_placeholder_index_is_rejected(self):
        att = self._attachment("invoice.pdf", _PDF_BYTES, "application/pdf")
        att.sudo().write({"index_content": "application"})
        with self._patch_index(return_value="application"):
            result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["structuredContent"]["format_used"], "link")
        self.assertNotEqual(result["content"][0]["text"], "application")

    def test_pdf_stored_index_content_used_without_reparse(self):
        att = self._attachment("invoice.pdf", _PDF_BYTES, "application/pdf")
        att.sudo().write({"index_content": "Stored invoice text"})
        with self._patch_index(side_effect=AssertionError("must not re-parse")):
            with self._forbid_payload_read():
                result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["content"][0]["text"], "Stored invoice text")
        self.assertEqual(result["structuredContent"]["format_used"], "extracted_text")

    def test_pdf_over_extract_cap_links_without_payload_read(self):
        att = self._attachment("huge.pdf", _PDF_BYTES, "application/pdf")
        self._fake_file_size(att, mcp_mixin.MAX_EXTRACT_BYTES + 1)
        with self._patch_index(side_effect=AssertionError("must not parse")):
            with self._forbid_payload_read():
                result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["structuredContent"]["format_used"], "link")
        self.assertIn("too large", result["content"][0]["text"])

    def test_index_failure_is_logged_and_falls_back_to_link(self):
        att = self._attachment("broken.pdf", _PDF_BYTES, "application/pdf")
        att.sudo().write({"index_content": False})
        with self._patch_index(side_effect=RuntimeError("parser exploded")):
            with self.assertLogs(_MIXIN_LOGGER, level="WARNING") as logs:
                result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["structuredContent"]["format_used"], "link")
        self.assertTrue(any("extraction failed" in line for line in logs.output))

    def test_other_mimetype_returns_link(self):
        att = self._attachment("archive.zip", b"PK\x03\x04junk", "application/zip")
        with self._forbid_payload_read():
            result = self.mixin.read_attachment(attachment_id=att.id)
        structured = result["structuredContent"]
        self.assertEqual(structured["format_used"], "link")
        self.assertIn("Download URL:", result["content"][0]["text"])

    def test_url_attachment_returns_external_url(self):
        att = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "link",
                    "type": "url",
                    "url": "https://example.com/doc",
                    "res_model": "res.partner",
                    "res_id": self.partner.id,
                }
            )
        )
        for fmt in ("auto", "link", "blob"):
            result = self.mixin.read_attachment(attachment_id=att.id, format=fmt)
            structured = result["structuredContent"]
            self.assertEqual(structured["format_used"], "url", fmt)
            self.assertEqual(structured["url"], "https://example.com/doc")
            self.assertEqual(structured["type"], "url")
            self.assertIn("untrusted", result["content"][0]["text"])

    def test_oversized_text_is_truncated_with_link(self):
        raw = b"a" * (mcp_mixin.MAX_INLINE_TEXT_CHARS + 100)
        att = self._attachment("big.txt", raw, "text/plain")
        result = self.mixin.read_attachment(attachment_id=att.id)
        structured = result["structuredContent"]
        self.assertEqual(structured["format_used"], "text")
        self.assertTrue(structured["truncated"])
        self.assertIn("download_path", structured)
        text = result["content"][0]["text"]
        self.assertIn("[Truncated after", text)
        self.assertTrue(text.startswith("a" * 100))

    def test_text_over_byte_cap_links_without_payload_read(self):
        att = self._attachment("giant.txt", b"x", "text/plain")
        self._fake_file_size(att, mcp_mixin.MAX_INLINE_TEXT_BYTES + 1)
        with self._forbid_payload_read():
            result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["structuredContent"]["format_used"], "link")

    def test_empty_attachment(self):
        att = self._attachment("empty.txt", b"", "text/plain")
        result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["content"][0]["text"], "")
        self.assertEqual(result["structuredContent"]["file_size"], 0)


class TestReadAttachmentLink(TestAttachmentToolsBase):
    """``read_attachment`` with ``format='link'``."""

    def test_link_performs_no_mutation_and_no_payload_read(self):
        att = self._attachment("doc.pdf", _PDF_BYTES, "application/pdf")
        before = att.sudo().read(["write_date", "checksum", "store_fname"])[0]
        count_before = self.env["ir.attachment"].sudo().search_count([])
        with self._forbid_payload_read():
            result = self.mixin.read_attachment(attachment_id=att.id, format="link")
        after = att.sudo().read(["write_date", "checksum", "store_fname"])[0]
        self.assertEqual(before, after)
        self.assertEqual(
            self.env["ir.attachment"].sudo().search_count([]), count_before
        )
        self.assertEqual(result["structuredContent"]["format_used"], "link")

    def test_attachment_link_shape(self):
        att = self._attachment("doc.pdf", _PDF_BYTES, "application/pdf")
        result = self.mixin.read_attachment(attachment_id=att.id, format="link")
        structured = result["structuredContent"]
        path = structured["download_path"]
        self.assertTrue(
            path.startswith(f"/mcp/download/ir.attachment/{att.id}/raw?access_token=")
        )
        self.assertEqual(structured["download_url"], f"https://odoo.example.com{path}")
        self.assertIn("expires_at", structured)
        self.assertIn(structured["download_url"], result["content"][0]["text"])
        token = path.split("access_token=", 1)[1].split("&", 1)[0]
        self.assertTrue(verify_download_token(att, "raw", token))

    def test_record_field_link_shape_without_payload_read(self):
        uri = f"odoo://record/res.partner/{self.partner.id}/image_1920"
        with self._forbid_payload_read():
            result = self.mixin.read_attachment(uri=uri, format="link")
        structured = result["structuredContent"]
        path = structured["download_path"]
        self.assertTrue(
            path.startswith(
                f"/mcp/download/res.partner/{self.partner.id}/image_1920"
                "?access_token="
            )
        )
        token = path.split("access_token=", 1)[1].split("&", 1)[0]
        self.assertTrue(verify_download_token(self.partner, "image_1920", token))

    def test_link_without_base_url_keeps_relative_path(self):
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "")
        att = self._attachment("doc.pdf", _PDF_BYTES, "application/pdf")
        result = self.mixin.read_attachment(attachment_id=att.id, format="link")
        structured = result["structuredContent"]
        self.assertIsNone(structured["download_url"])
        self.assertIn(structured["download_path"], result["content"][0]["text"])

    def _assert_ttl(self, param_value, expected_ttl):
        params = self.env["ir.config_parameter"].sudo()
        if param_value is None:
            params.set_param("mcp_server.link_ttl_hours", "")
        else:
            params.set_param("mcp_server.link_ttl_hours", param_value)
        att = self._attachment("ttl.pdf", _PDF_BYTES, "application/pdf")
        start = int(time.time())
        result = self.mixin.read_attachment(attachment_id=att.id, format="link")
        structured = result["structuredContent"]
        expiry = self._token_expiry(structured["download_path"])
        self.assertAlmostEqual(expiry, start + expected_ttl, delta=5)
        parsed = datetime.strptime(
            structured["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        self.assertEqual(int(parsed.timestamp()), expiry)

    def test_ttl_default(self):
        self._assert_ttl(None, DEFAULT_LINK_TTL_HOURS * 3600)

    def test_ttl_configured(self):
        self._assert_ttl("24", 24 * 3600)

    def test_ttl_malformed_and_non_positive_fall_back(self):
        self._assert_ttl("abc", DEFAULT_LINK_TTL_HOURS * 3600)
        self._assert_ttl("-5", DEFAULT_LINK_TTL_HOURS * 3600)
        self._assert_ttl("0", DEFAULT_LINK_TTL_HOURS * 3600)

    def test_ttl_clamped_to_core_ceiling(self):
        self._assert_ttl(str(MAX_LINK_TTL_HOURS * 10), MAX_LINK_TTL_HOURS * 3600)


class TestReadAttachmentBlob(TestAttachmentToolsBase):
    """``read_attachment`` with ``format='blob'``."""

    def test_blob_is_embedded_resource(self):
        att = self._attachment("pixel.png", _PNG_1X1, "image/png")
        result = self.mixin.read_attachment(attachment_id=att.id, format="blob")
        self.assertEqual(result["structuredContent"]["format_used"], "blob")
        resource = result["content"][1]
        self.assertEqual(resource["type"], "resource")
        self.assertEqual(
            resource["resource"],
            {
                "uri": f"odoo://attachment/{att.id}",
                "mimeType": "image/png",
                "blob": base64.b64encode(_PNG_1X1).decode("ascii"),
            },
        )

    def test_blob_textual_mimetype_yields_text_resource(self):
        att = self._attachment("data.json", b'{"a": 1}', "application/json")
        result = self.mixin.read_attachment(attachment_id=att.id, format="blob")
        resource = result["content"][1]["resource"]
        self.assertEqual(resource["text"], '{"a": 1}')
        self.assertNotIn("blob", resource)

    def test_blob_over_cap_is_refused_without_payload_read(self):
        att = self._attachment("big.bin", b"x", "application/octet-stream")
        self._fake_file_size(att, mcp_mixin.MAX_INLINE_BLOB_BYTES + 1)
        with self._forbid_payload_read():
            with self.assertRaises(UserError) as ctx:
                self.mixin.read_attachment(attachment_id=att.id, format="blob")
        self.assertIn("format='link'", str(ctx.exception))


class TestReadAttachmentGating(TestAttachmentToolsBase):
    """MCP allow-list gate, input validation and Odoo ACL binding."""

    def _users_attachment(self):
        """A public attachment on a non-enabled model (res.users)."""
        return (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "gated.txt",
                    "raw": b"gated",
                    "mimetype": "text/plain",
                    "public": True,
                    "res_model": "res.users",
                    "res_id": self.mcp_user.id,
                }
            )
        )

    def test_non_enabled_parent_is_denied(self):
        att = self._users_attachment()
        with self.assertRaises(AccessError):
            self.mixin.read_attachment(attachment_id=att.id)

    def test_enabled_ir_attachment_alone_grants_access(self):
        att = self._users_attachment()
        self._enable_model("base.model_ir_attachment", allow_read=True)
        result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["content"][0]["text"], "gated")

    def test_enabled_parent_grants_access(self):
        att = self._users_attachment()
        self._enable_model("base.model_res_users", allow_read=True)
        result = self.mixin.read_attachment(attachment_id=att.id)
        self.assertEqual(result["content"][0]["text"], "gated")

    def test_record_uri_requires_parent_model_even_with_ir_attachment(self):
        """ir.attachment enablement never unlocks arbitrary binary fields."""
        self._enable_model("base.model_ir_attachment", allow_read=True)
        uri = f"odoo://record/res.users/{self.mcp_user.id}/image_1920"
        with self.assertRaises(AccessError):
            self.mixin.read_attachment(uri=uri)

    def test_empty_record_field_is_missing_in_every_format(self):
        """An unset binary field raises MissingError like resources/read."""
        partner = self.env["res.partner"].create({"name": "No image"})
        uri = f"odoo://record/res.partner/{partner.id}/image_1920"
        for fmt in ("auto", "link", "blob"):
            with self.subTest(fmt), self._forbid_payload_read():
                with self.assertRaises(MissingError):
                    self.mixin.read_attachment(uri=uri, format=fmt)
        # Plain binary column (no backing attachment) behaves the same.
        self._enable_model("base.model_res_company", allow_read=True)
        company = self.env.company
        company.sudo().write({"logo_web": False})
        with self.assertRaises(MissingError):
            self.mixin.read_attachment(
                uri=f"odoo://record/res.company/{company.id}/logo_web"
            )

    def test_uri_and_attachment_id_are_exclusive(self):
        att = self._attachment("note.txt", b"x", "text/plain")
        with self.assertRaises(UserError):
            self.mixin.read_attachment(
                uri=f"odoo://attachment/{att.id}", attachment_id=att.id
            )
        with self.assertRaises(UserError):
            self.mixin.read_attachment()

    def test_invalid_inputs(self):
        with self.assertRaises(UserError):
            self.mixin.read_attachment(uri="https://example.com/x")
        with self.assertRaises(UserError):
            self.mixin.read_attachment(uri="odoo://unknown/1")
        with self.assertRaises(UserError):
            self.mixin.read_attachment(attachment_id="abc")
        att = self._attachment("note.txt", b"x", "text/plain")
        with self.assertRaises(UserError):
            self.mixin.read_attachment(attachment_id=att.id, format="zip")

    def test_missing_attachment(self):
        self._enable_model("base.model_ir_attachment", allow_read=True)
        with self.assertRaises(MissingError):
            self.mixin.read_attachment(attachment_id=999999999)
        with self.assertRaises(MissingError):
            self.mixin.read_attachment(uri="odoo://attachment/999999999")

    def test_acl_denial_for_private_attachment(self):
        """Gate passes (ir.attachment enabled) but Odoo's ACL still binds."""
        self._enable_model("base.model_ir_attachment", allow_read=True)
        private = (
            self.env["ir.attachment"]
            .sudo()
            .create({"name": "private.txt", "raw": b"secret", "mimetype": "text/plain"})
        )
        with self.assertRaises(AccessError):
            self.mixin.read_attachment(attachment_id=private.id)
        with self.assertRaises(AccessError):
            self.mixin.read_attachment(attachment_id=private.id, format="link")


class TestListRecordAttachments(TestAttachmentToolsBase):
    """``list_record_attachments``: listing, pagination and gating."""

    def test_lists_metadata_and_uris(self):
        text = self._attachment("a.txt", b"aaa", "text/plain")
        link = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "site",
                    "type": "url",
                    "url": "https://example.com",
                    "res_model": "res.partner",
                    "res_id": self.partner.id,
                }
            )
        )
        result = self.mixin.list_record_attachments("res.partner", self.partner.id)
        structured = result["structuredContent"]
        self.assertEqual(structured["total"], 2)
        self.assertEqual(structured["model"], "res.partner")
        self.assertEqual(structured["record_id"], self.partner.id)
        by_id = {entry["id"]: entry for entry in structured["attachments"]}
        self.assertEqual(
            by_id[text.id],
            {
                "id": text.id,
                "name": "a.txt",
                "mimetype": "text/plain",
                "type": "binary",
                "file_size": 3,
                "create_date": by_id[text.id]["create_date"],
                "create_uid": text.create_uid.display_name,
                "uri": f"odoo://attachment/{text.id}",
            },
        )
        self.assertEqual(by_id[link.id]["type"], "url")
        # A null mimetype is normalised to None (never the ORM's ``False``).
        self.assertIsNot(by_id[link.id]["mimetype"], False)
        text_out = result["content"][0]["text"]
        self.assertIn(f"odoo://attachment/{text.id}", text_out)
        self.assertIn("read_attachment", text_out)

    def test_field_backed_blobs_are_excluded(self):
        # The partner image lives in res_field-backed attachments.
        result = self.mixin.list_record_attachments("res.partner", self.partner.id)
        self.assertEqual(result["structuredContent"]["attachments"], [])
        self.assertEqual(result["structuredContent"]["total"], 0)
        self.assertIn("No attachments.", result["content"][0]["text"])

    def test_pagination_clamps_limit_and_bounds_offset(self):
        for idx in range(4):
            self._attachment(f"f{idx}.txt", b"x", "text/plain")
        self.env["ir.config_parameter"].sudo().set_param("mcp_server.max_limit", "2")
        result = self.mixin.list_record_attachments(
            "res.partner", self.partner.id, limit=50
        )
        structured = result["structuredContent"]
        self.assertEqual(structured["limit"], 2)
        self.assertEqual(len(structured["attachments"]), 2)
        self.assertEqual(structured["total"], 4)
        self.assertIn("offset=2", result["content"][0]["text"])

        page2 = self.mixin.list_record_attachments(
            "res.partner", self.partner.id, limit=2, offset=2
        )
        self.assertEqual(len(page2["structuredContent"]["attachments"]), 2)
        ids = {e["id"] for e in structured["attachments"]} | {
            e["id"] for e in page2["structuredContent"]["attachments"]
        }
        self.assertEqual(len(ids), 4)

        bounded = self.mixin.list_record_attachments(
            "res.partner", self.partner.id, offset=999999999
        )
        self.assertEqual(bounded["structuredContent"]["offset"], 2 * 1000)

    def test_gating_requires_parent_or_ir_attachment(self):
        with self.assertRaises(AccessError):
            self.mixin.list_record_attachments("res.users", self.mcp_user.id)
        self._enable_model("base.model_ir_attachment", allow_read=True)
        result = self.mixin.list_record_attachments("res.users", self.mcp_user.id)
        self.assertEqual(result["structuredContent"]["total"], 0)

    def test_unknown_model_and_missing_record(self):
        with self.assertRaises(UserError):
            self.mixin.list_record_attachments("no.such.model", 1)
        with self.assertRaises(MissingError):
            self.mixin.list_record_attachments("res.partner", 999999999)

    def test_acl_filters_attachments_the_user_cannot_read(self):
        """A parent the user cannot read is refused; hidden rows never show."""
        self._enable_model("base.model_ir_attachment", allow_read=True)
        self._enable_model("base.model_ir_mail_server", allow_read=True)
        server = (
            self.env["ir.mail_server"]
            .sudo()
            .create({"name": "mcp-test-smtp", "smtp_host": "localhost"})
        )
        self.env["ir.attachment"].sudo().create(
            {
                "name": "cfg.txt",
                "raw": b"x",
                "mimetype": "text/plain",
                "res_model": "ir.mail_server",
                "res_id": server.id,
            }
        )
        # ir.mail_server is admin-only: the parent read check denies.
        with self.assertRaises(AccessError):
            self.mixin.list_record_attachments("ir.mail_server", server.id)


class TestUploadAttachment(TestAttachmentToolsBase):
    """``upload_attachment``: gating, validation and round-trip."""

    _DATA = base64.b64encode(b"hello upload").decode("ascii")

    def test_record_linked_upload_with_parent_write(self):
        self._enable_model("base.model_res_partner", allow_read=True, allow_write=True)
        result = self.mixin.upload_attachment(
            "note.txt", self._DATA, model="res.partner", record_id=self.partner.id
        )
        structured = result["structuredContent"]
        att = self.env["ir.attachment"].sudo().browse(structured["attachment_id"])
        self.assertEqual(att.raw, b"hello upload")
        self.assertEqual(att.res_model, "res.partner")
        self.assertEqual(att.res_id, self.partner.id)
        self.assertEqual(att.type, "binary")
        self.assertEqual(att.mimetype, "text/plain")
        self.assertEqual(att.create_uid, self.mcp_user)
        self.assertEqual(
            structured,
            {
                "attachment_id": att.id,
                "uri": f"odoo://attachment/{att.id}",
                "name": "note.txt",
                "mimetype": "text/plain",
                "file_size": 12,
                "model": "res.partner",
                "record_id": self.partner.id,
            },
        )
        self.assertIn(structured["uri"], result["content"][0]["text"])
        # Round trip: the parent is read-enabled, so the uri resolves.
        read = self.mixin.read_attachment(uri=structured["uri"])
        self.assertEqual(read["content"][0]["text"], "hello upload")

    def test_record_linked_upload_without_parent_write_is_denied(self):
        # res.partner is read-only enabled (setUp).
        with self.assertRaises(AccessError):
            self.mixin.upload_attachment(
                "note.txt", self._DATA, model="res.partner", record_id=self.partner.id
            )
        # ir.attachment create enablement does not substitute for parent write.
        self._enable_model("base.model_ir_attachment", allow_create=True)
        with self.assertRaises(AccessError):
            self.mixin.upload_attachment(
                "note.txt", self._DATA, model="res.partner", record_id=self.partner.id
            )

    def test_record_linked_upload_does_not_need_ir_attachment_create(self):
        """Attaching is part of writing the parent: no ir.attachment gate."""
        self._enable_model("base.model_res_partner", allow_read=True, allow_write=True)
        self._disable_model("base.model_ir_attachment")
        result = self.mixin.upload_attachment(
            "note.txt", self._DATA, model="res.partner", record_id=self.partner.id
        )
        self.assertTrue(result["structuredContent"]["attachment_id"])

    def test_standalone_upload_requires_ir_attachment_create(self):
        with self.assertRaises(AccessError):
            self.mixin.upload_attachment("loose.txt", self._DATA)
        self._enable_model("base.model_ir_attachment", allow_create=True)
        result = self.mixin.upload_attachment("loose.txt", self._DATA)
        structured = result["structuredContent"]
        att = self.env["ir.attachment"].sudo().browse(structured["attachment_id"])
        self.assertFalse(att.res_model)
        self.assertNotIn("model", structured)

    def test_explicit_mimetype_and_guess(self):
        self._enable_model("base.model_ir_attachment", allow_create=True)
        pdf = base64.b64encode(_PDF_BYTES).decode("ascii")
        explicit = self.mixin.upload_attachment(
            "doc.bin", pdf, mimetype="application/pdf"
        )
        self.assertEqual(explicit["structuredContent"]["mimetype"], "application/pdf")
        guessed = self.mixin.upload_attachment("doc.pdf", pdf)
        self.assertEqual(guessed["structuredContent"]["mimetype"], "application/pdf")

    def test_dangerous_mimetype_downgraded_by_core(self):
        """Core stores HTML/SVG from non-privileged users as text/plain."""
        self._enable_model("base.model_ir_attachment", allow_create=True)
        html = base64.b64encode(b"<html><script>x</script></html>").decode("ascii")
        result = self.mixin.upload_attachment("page.html", html, mimetype="text/html")
        self.assertEqual(result["structuredContent"]["mimetype"], "text/plain")

    def test_validation_errors(self):
        self._enable_model("base.model_ir_attachment", allow_create=True)
        with self.assertRaises(UserError):
            self.mixin.upload_attachment("x.txt", "not base64!!")
        with self.assertRaises(UserError):
            self.mixin.upload_attachment("x.txt", self._DATA, model="res.partner")
        with self.assertRaises(UserError):
            self.mixin.upload_attachment("x.txt", self._DATA, record_id=1)
        with self.assertRaises(UserError):
            self.mixin.upload_attachment("", self._DATA)
        with self.assertRaises(UserError):
            self.mixin.upload_attachment("x.txt", 123)

    def test_oversize_payload_is_refused(self):
        self._enable_model("base.model_ir_attachment", allow_create=True)
        from ..models.mcp_tools_write import MAX_UPLOAD_BYTES

        with patch.object(
            type(self.env["ir.attachment"]),
            "create",
            side_effect=AssertionError("must not create"),
        ):
            with self.assertRaises(UserError) as ctx:
                self.mixin.upload_attachment(
                    "big.bin",
                    base64.b64encode(b"\0" * (MAX_UPLOAD_BYTES + 1)).decode("ascii"),
                )
        self.assertIn("upload limit", str(ctx.exception))

    def test_acl_user_without_parent_write(self):
        """MCP gate passes but Odoo denies the write on the parent record."""
        self._enable_model(
            "base.model_ir_mail_server", allow_read=True, allow_write=True
        )
        server = (
            self.env["ir.mail_server"]
            .sudo()
            .create({"name": "mcp-upload-smtp", "smtp_host": "localhost"})
        )
        with self.assertRaises(AccessError):
            self.mixin.upload_attachment(
                "cfg.txt", self._DATA, model="ir.mail_server", record_id=server.id
            )


@tagged("much_unit", "post_install", "-at_install")
class TestAttachmentAuditAttribution(common.TransactionCase):
    """Audit ``model`` / record-id attribution for the attachment tools."""

    def test_extract_record_ids_from_attachment_arguments(self):
        extract = MCPController._extract_record_ids
        self.assertEqual(extract({"attachment_id": 7}), [7])
        self.assertEqual(extract({"uri": "odoo://attachment/9"}), [9])
        self.assertEqual(extract({"uri": "odoo://record/res.partner/3/image"}), [3])
        self.assertIsNone(extract({"uri": "garbage"}))
        self.assertIsNone(extract({"name": "x", "data": "y"}))
        # Existing keys keep precedence.
        self.assertEqual(extract({"record_id": 1, "attachment_id": 2}), [1])

    def test_attachment_audit_model(self):
        resolve = MCPController._attachment_audit_model
        self.assertEqual(
            resolve("read_attachment", {"attachment_id": 7}), "ir.attachment"
        )
        self.assertEqual(
            resolve("read_attachment", {"uri": "odoo://record/res.partner/3/image"}),
            "res.partner",
        )
        self.assertEqual(resolve("upload_attachment", {"name": "x"}), "ir.attachment")
        self.assertIsNone(resolve("get_record", {"record_id": 1}))
