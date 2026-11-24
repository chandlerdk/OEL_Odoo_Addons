"""Per-user MCP access-group gate on every door.

Membership in ``mcp_server.group_mcp_user`` is required to use MCP at all:
the native ``/mcp`` bearer door (API key AND OAuth token), the X-API-Key /
session REST routes, the XML-RPC proxy, the OAuth consent screen and the
token grants. A VALID credential whose user is not a member is refused with
403 (never 401 -- a 401 challenge would send a spec-following client into a
re-auth loop that only mints another refused token), and the refusal is
audited with the resolved user id.
"""

import json
import re
import secrets
import time
import xmlrpc.client as xmlrpclib
from unittest.mock import patch
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from odoo import fields
from odoo.tests import common, tagged

from ..controllers import auth, oauth_server, rate_limiting, utils
from ..models import ir_http
from .test_helpers import (
    UrlOpenCompatMixin,
    create_test_user,
    grant_mcp_access,
    users_groups_field,
)
from .test_oauth import _code_challenge, _sha256_hex

# Must match mcp_server/controllers/mcp.py.
PREFERRED_PROTOCOL_VERSION = "2025-11-25"


@tagged("much_unit", "post_install", "-at_install")
class TestMcpAccessGroupGate(UrlOpenCompatMixin, common.HttpCase):
    """The group gate refuses non-members on every MCP surface."""

    def setUp(self):
        super().setUp()
        utils.clear_mcp_caches()
        # Module-level limiters survive the per-test rollback; reset them so a
        # stale count from a sibling test cannot swallow an expected audit row.
        rate_limiting._api_limiter.clear()
        ir_http._bearer_failure_limiter.clear()
        auth._auth_failure_limiter.clear()
        oauth_server._dcr_limiter.clear()

        unique_id = str(int(time.time() * 1000))[-6:]
        self.password = "gate_pw"  # nosec B105 - test fixture credential

        # An ordinary internal user NOT in the MCP access group...
        self.nogroup_login = f"mcp_gate_out_{unique_id}"
        self.user_nogroup = create_test_user(
            self.env,
            "MCP Gate Outsider",
            self.nogroup_login,
            password=self.password,
            email=f"mcp_gate_out_{unique_id}@example.com",
        )
        self.key_nogroup = self._mint_key(self.user_nogroup, "Gate Outsider Key")

        # ...and one that IS a member.
        self.ingroup_login = f"mcp_gate_in_{unique_id}"
        self.user_ingroup = create_test_user(
            self.env,
            "MCP Gate Member",
            self.ingroup_login,
            password=self.password,
            email=f"mcp_gate_in_{unique_id}@example.com",
        )
        grant_mcp_access(self.user_ingroup)
        self.key_ingroup = self._mint_key(self.user_ingroup, "Gate Member Key")

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("mcp_server.enable_oauth", "True")
        params.set_param("mcp_server.enable_logging", "True")
        self._enable_model("base.model_res_partner", allow_read=True)
        utils.clear_mcp_caches()

        # OAuth audience/redirect fixtures (RFC 8707 audience = this resource).
        self.resource = self.base_url() + "/mcp"
        self.redirect_uri = "http://127.0.0.1:8765/callback"

    # ------------------------------------------------------------------
    # Fixture helpers
    # ------------------------------------------------------------------
    def _mint_key(self, user, name):
        """Mint an ``rpc``-scope API key for ``user``."""
        return self.env(user=user)["res.users.apikeys"]._generate("rpc", name)

    def _enable_model(self, model_xmlid, **perms):
        """Find-or-create an ``mcp.enabled.model`` row for ``model_xmlid``."""
        model_id = self.env.ref(model_xmlid).id
        record = (
            self.env["mcp.enabled.model"]
            .sudo()
            .with_context(active_test=False)
            .search([("model_id", "=", model_id)], limit=1)
        )
        vals = {"active": True, **perms}
        if record:
            record.write(vals)
        else:
            record = (
                self.env["mcp.enabled.model"]
                .sudo()
                .create({"model_id": model_id, **vals})
            )
        return record

    def _remove_mcp_group(self, user):
        """UNLINK the MCP access group from ``user`` (default groups stay)."""
        group = self.env.ref("mcp_server.group_mcp_user")
        user.write({users_groups_field(self.env): [(3, group.id)]})

    def _post_rpc(self, api_key, body=None):
        """POST a JSON-RPC ``body`` (default: ping) to ``/mcp`` with a bearer."""
        body = body or {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        return self.url_open("/mcp", data=json.dumps(body), headers=headers)

    def _register_client(self):
        """Register a public PKCE client via RFC 7591 DCR; return client_id."""
        response = self.url_open(
            "/mcp/oauth/register",
            json={
                "redirect_uris": [self.redirect_uri],
                "client_name": "Gate Test Client",
                "scope": "mcp",
            },
        )
        self.assertIn(response.status_code, (200, 201), response.text[:500])
        return response.json()["client_id"]

    def _authorize_params(self, client_id, challenge):
        """Assemble a valid PKCE-S256 authorization request."""
        return {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "mcp",
            "state": "state-gate",
            "resource": self.resource,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

    def _approve_consent(self, params):
        """Render the consent page, approve it with CSRF, return the code."""
        get_resp = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(get_resp.status_code, 200, get_resp.text[:500])
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
        self.assertIsNotNone(match, "consent page must embed a CSRF token")
        post_data = dict(params, csrf_token=match.group(1), decision="allow")
        post_resp = self.url_open(
            "/mcp/oauth/authorize", data=post_data, allow_redirects=False
        )
        self.assertEqual(post_resp.status_code, 302, post_resp.text[:500])
        query = dict(parse_qsl(urlsplit(post_resp.headers["Location"]).query))
        self.assertIn("code", query, post_resp.headers.get("Location"))
        return query["code"]

    def _exchange_code(self, client_id, code, verifier):
        """Swap an authorization code for tokens; return the raw response."""
        return self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": self.resource,
            },
            allow_redirects=False,
        )

    # ------------------------------------------------------------------
    # Native /mcp bearer door -- API-key front door
    # ------------------------------------------------------------------
    def test_api_key_of_non_member_refused_403(self):
        """A VALID rpc key owned by a non-member is refused with 403, not 401.

        403 (no ``WWW-Authenticate`` challenge) so a spec-following MCP client
        surfaces the error instead of looping through re-auth.
        """
        response = self._post_rpc(self.key_nogroup)
        self.assertEqual(response.status_code, 403, response.text[:300])
        self.assertNotIn("WWW-Authenticate", response.headers)

    def test_api_key_of_member_accepted(self):
        """The same door admits a group member (positive control)."""
        response = self._post_rpc(
            self.key_ingroup,
            body={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": PREFERRED_PROTOCOL_VERSION},
            },
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertIn("result", response.json())

    def test_group_removal_cuts_off_outstanding_key(self):
        """Removing the group refuses the user's EXISTING key immediately.

        The gate runs at credential USE time (and core invalidates the group
        cache on the write), so no re-issue or restart window remains open.
        """
        self.assertEqual(self._post_rpc(self.key_ingroup).status_code, 200)
        self._remove_mcp_group(self.user_ingroup)
        self.assertEqual(self._post_rpc(self.key_ingroup).status_code, 403)

    def test_group_side_removal_cuts_off_outstanding_key(self):
        """Removing the user on the GROUP form refuses the existing key too.

        Group-side revocation (``res.groups.write({'users': ...})``) is a
        different write path than the user-side groups unlink the
        sibling test exercises; this pins that it also bites immediately at
        credential use time, after the gate warmed on a prior request.
        """
        self.assertEqual(self._post_rpc(self.key_ingroup).status_code, 200)
        group = self.env.ref("mcp_server.group_mcp_user").sudo()
        field = "user_ids" if "user_ids" in group._fields else "users"
        group.write({field: [(3, self.user_ingroup.id)]})
        self.assertEqual(self._post_rpc(self.key_ingroup).status_code, 403)

    # ------------------------------------------------------------------
    # Native /mcp bearer door -- OAuth front door
    # ------------------------------------------------------------------
    def test_oauth_token_of_non_member_refused_403(self):
        """A live, audience-bound OAuth token of a non-member is refused (403)."""
        unique_id = str(int(time.time() * 1000))[-6:]
        client = (
            self.env["mcp.oauth.client"]
            .sudo()
            .create(
                {
                    "client_id": f"gate-client-{unique_id}",
                    "redirect_uris": self.redirect_uri,
                }
            )
        )
        raw = secrets.token_urlsafe(48)
        self.env["mcp.oauth.token"].sudo().create(
            {
                "access_token_hash": _sha256_hex(raw),
                "client": client.id,
                "user_id": self.user_nogroup.id,
                "scope": "mcp",
                "audience": self.resource,
                "access_expires_at": fields.Datetime.now() + timedelta(hours=1),
            }
        )
        response = self._post_rpc(raw)
        self.assertEqual(response.status_code, 403, response.text[:300])

    # ------------------------------------------------------------------
    # Legacy REST routes (X-API-Key header and session cookie doors)
    # ------------------------------------------------------------------
    def test_rest_api_key_of_non_member_403_and_audited(self):
        """The REST door refuses a non-member (403) and audits WHO was refused.

        This route returns (rather than raises), so the fresh-cursor audit row
        stays observable under HttpCase -- assert it here for every door.
        """
        domain = [
            ("event_type", "=", "auth_failure"),
            ("user_id", "=", self.user_nogroup.id),
            ("error_message", "=", auth.MCP_GROUP_DENIED_MESSAGE),
        ]
        log_model = self.env["mcp.log"].sudo()
        before = log_model.search_count(domain)

        response = self.url_open(
            "/mcp/models",
            headers={"X-API-Key": self.key_nogroup, "Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 403, response.text[:300])
        payload = json.loads(response.text)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "E403")

        self.env.invalidate_all()
        self.assertEqual(
            log_model.search_count(domain),
            before + 1,
            "the group refusal must persist an attributed auth_failure row",
        )

    def test_rest_session_of_non_member_403(self):
        """A logged-in session of a non-member is refused on the REST routes."""
        self.authenticate(self.nogroup_login, self.password)
        response = self.url_open("/mcp/models", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 403, response.text[:300])

    def test_rest_session_denial_row_not_attributed_to_api_key(self):
        """A stale key beside a valid session is audited as a session refusal.

        ``require_auth`` records WHICH credential resolved the refused user,
        not whether an X-API-Key header was merely present: an invalid key
        alongside a valid session cookie authenticates via the session, and
        the denial row must say ``api_key_used=False``.
        """
        domain = [
            ("event_type", "=", "auth_failure"),
            ("user_id", "=", self.user_nogroup.id),
            ("error_message", "=", auth.MCP_GROUP_DENIED_MESSAGE),
            ("api_key_used", "=", False),
        ]
        log_model = self.env["mcp.log"].sudo()
        before = log_model.search_count(domain)

        self.authenticate(self.nogroup_login, self.password)
        response = self.url_open(
            "/mcp/models",
            headers={
                "X-API-Key": "stale-key-no-longer-valid-0000000000",
                "Accept": "application/json",
            },
        )
        self.assertEqual(response.status_code, 403, response.text[:300])

        self.env.invalidate_all()
        self.assertEqual(
            log_model.search_count(domain),
            before + 1,
            "a session-resolved refusal must not be attributed to the API key",
        )

    # ------------------------------------------------------------------
    # Legacy XML-RPC proxy
    # ------------------------------------------------------------------
    def test_xmlrpc_proxy_non_member_denied(self):
        """The /mcp/xmlrpc/object proxy faults (403) for a non-member's key."""
        request_data = xmlrpclib.dumps(
            (
                self.env.cr.dbname,
                self.user_nogroup.id,
                self.key_nogroup,
                "res.partner",
                "search_count",
                [[]],
            ),
            "execute_kw",
            allow_none=1,
        )
        response = self.url_open(
            "/mcp/xmlrpc/object",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )
        with self.assertRaises(xmlrpclib.Fault) as cm:
            xmlrpclib.loads(response.content)
        self.assertEqual(cm.exception.faultCode, 403)
        self.assertIn("MCP User group", cm.exception.faultString)

    def _xmlrpc_fault(self, uid, password):
        """POST an execute_kw with (uid, password); return the raised Fault."""
        request_data = xmlrpclib.dumps(
            (self.env.cr.dbname, uid, password, "res.partner", "search_count", [[]]),
            "execute_kw",
            allow_none=1,
        )
        response = self.url_open(
            "/mcp/xmlrpc/object",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )
        with self.assertRaises(xmlrpclib.Fault) as cm:
            xmlrpclib.loads(response.content)
        return cm.exception

    def test_xmlrpc_bad_password_gives_no_membership_oracle(self):
        """A bad password must not leak whether a uid is an MCP non-member.

        The gate never inspects the raw client-supplied uid before the
        password is verified, so a garbage credential is refused by core, not
        by the gate: against a real non-member uid it faults with the same
        fault code as against a non-existent uid -- no 403-vs-500 split to
        sweep the uid space with.
        """
        garbage = "garbage-token-not-a-real-key-000000"  # >20 chars: key path
        nonmember = self._xmlrpc_fault(self.user_nogroup.id, garbage)
        ghost = self._xmlrpc_fault(9_999_999, garbage)
        self.assertEqual(nonmember.faultCode, ghost.faultCode)
        self.assertNotIn("MCP User group", nonmember.faultString)

    def test_xmlrpc_password_non_member_refused(self):
        """A VALID uid/password whose user is not a member is refused 403.

        The proxy's second credential kind. Gated only after core's own
        ``res.users.check`` verified the password, so reaching this 403
        requires already holding the credential.
        """
        fault = self._xmlrpc_fault(self.user_nogroup.id, self.password)
        self.assertEqual(fault.faultCode, 403)
        self.assertIn("MCP User group", fault.faultString)

    def test_xmlrpc_password_member_is_not_gated(self):
        """A member's VALID uid/password gets past the gate (positive control).

        Pins that the added credential verification does not refuse the path it
        gates -- without it, a gate that refused everyone would still pass the
        negative test above. On Odoo 17 the HttpCase test cursor is not
        read-only, so the call returns its result.
        """
        request_data = xmlrpclib.dumps(
            (
                self.env.cr.dbname,
                self.user_ingroup.id,
                self.password,
                "res.partner",
                "search_count",
                [[]],
            ),
            "execute_kw",
            allow_none=1,
        )
        response = self.url_open(
            "/mcp/xmlrpc/object",
            data=request_data,
            headers={"Content-Type": "text/xml"},
        )
        (count,), _method = xmlrpclib.loads(response.content)
        self.assertIsInstance(count, int)

    def test_xmlrpc_password_gate_normalises_the_uid(self):
        """A non-member is refused however the uid is encoded on the wire.

        ``model_service_root.dispatch`` normalises with ``int(uid)`` before it
        authenticates, so a gate that only recognised a Python ``int`` would
        let ``<string>42</string>`` or ``<double>42.0</double>`` walk past and
        still authenticate downstream.
        """
        for encoded in (str(self.user_nogroup.id), float(self.user_nogroup.id)):
            with self.subTest(uid=encoded):
                fault = self._xmlrpc_fault(encoded, self.password)
                self.assertEqual(fault.faultCode, 403)
                self.assertIn("MCP User group", fault.faultString)

    def test_xmlrpc_failed_password_still_audited(self):
        """A rejected uid/password on the proxy still reaches the audit write.

        The gate verifies the credential above the dispatch try/except, so
        without an explicit write the ``AccessDenied`` would fault the request
        with no audit trace at all -- the only MCP door with no record of the
        refusal. Asserted via mock, not a row count: this route is readonly,
        so under HttpCase the request-cursor audit write is refused and
        swallowed (production uses the independent committed cursor). The call
        is unattributed (no ``user_id``): the uid is client-supplied and
        unverified, so attributing it would let a caller seed the audit log
        with arbitrary identities.
        """
        wrong = "definitely-wrong"  # nosec B105 - test fixture, <=20 chars
        with patch.object(auth, "_log_auth_failure") as mocked:
            fault = self._xmlrpc_fault(self.user_ingroup.id, wrong)
        self.assertNotIn("MCP User group", fault.faultString)
        mocked.assert_called_once_with(
            "Invalid uid/password credential", api_key_used=False
        )

    def test_xmlrpc_wrong_password_gives_no_membership_oracle(self):
        """A wrong password faults the same for member, non-member and ghost.

        The gate verifies the credential before it reads membership, so a
        caller without the password learns nothing -- the 403 is reachable
        only once the password is proven. Complements
        ``test_xmlrpc_bad_password_gives_no_membership_oracle``, which covers
        the >20-char API-key-shaped token; this one covers a short password.
        """
        wrong = "definitely-not-the-password"  # nosec B105 - test fixture
        member = self._xmlrpc_fault(self.user_ingroup.id, wrong)
        nonmember = self._xmlrpc_fault(self.user_nogroup.id, wrong)
        ghost = self._xmlrpc_fault(9_999_999, wrong)
        self.assertEqual(member.faultCode, nonmember.faultCode)
        self.assertEqual(nonmember.faultCode, ghost.faultCode)
        for fault in (member, nonmember, ghost):
            self.assertNotIn("MCP User group", fault.faultString)

    def _fake_common_dispatch(self, method, params):
        """Stand-in for core's ``common`` service.

        Core's ``exp_authenticate`` opens its own read/write registry cursor,
        which HttpCase refuses from this readonly route, so the verification
        is emulated: the uid for the right password, ``False`` otherwise.
        """
        if method == "version":
            return {"server_version": "test"}
        login, password = params[1], params[2]
        user = self.env["res.users"].search([("login", "=", login)])
        return user.id if password == self.password else False

    def _xmlrpc_common(self, method, *params):
        """POST a ``common`` service call through the emulated core service."""
        with patch("odoo.service.common.dispatch", self._fake_common_dispatch):
            return self.url_open(
                "/mcp/xmlrpc/common",
                data=xmlrpclib.dumps(params, method, allow_none=1),
                headers={"Content-Type": "text/xml"},
            )

    def test_xmlrpc_common_authenticate_non_member_refused(self):
        """``common.authenticate`` refuses a non-member's VALID credential (403).

        Gated like the object proxy, and only after core verified the
        credential: a non-member with the right password gets the 403 fault
        and a group-denied audit call instead of their uid; a member gets
        their uid.
        """
        db = self.env.cr.dbname
        for method, extra in (("authenticate", ({},)), ("login", ())):
            with self.subTest(method=method):
                with patch.object(auth, "log_mcp_group_denied") as denied:
                    response = self._xmlrpc_common(
                        method, db, self.nogroup_login, self.password, *extra
                    )
                with self.assertRaises(xmlrpclib.Fault) as cm:
                    xmlrpclib.loads(response.content)
                self.assertEqual(cm.exception.faultCode, 403)
                self.assertIn("MCP User group", cm.exception.faultString)
                denied.assert_called_once()
                self.assertEqual(denied.call_args.args[0], self.user_nogroup)

                response = self._xmlrpc_common(
                    method, db, self.ingroup_login, self.password, *extra
                )
                self.assertEqual(
                    xmlrpclib.loads(response.content)[0][0], self.user_ingroup.id
                )

    def test_xmlrpc_calls_naming_another_database_refused(self):
        """A call naming another database than the request's is refused.

        The MCP gates read the request's database while core would verify and
        run the call in the named one, so the proxy serves its own database only.
        """
        other = self.env.cr.dbname + "_other"
        response = self._xmlrpc_common(
            "authenticate", other, self.ingroup_login, self.password, {}
        )
        with self.assertRaises(xmlrpclib.Fault) as cm:
            xmlrpclib.loads(response.content)
        self.assertEqual(cm.exception.faultCode, 400)
        self.assertIn("does not serve", cm.exception.faultString)

        response = self.url_open(
            "/mcp/xmlrpc/object",
            data=xmlrpclib.dumps(
                (
                    other,
                    self.user_ingroup.id,
                    self.password,
                    "res.partner",
                    "search_count",
                    [[]],
                ),
                "execute_kw",
                allow_none=1,
            ),
            headers={"Content-Type": "text/xml"},
        )
        with self.assertRaises(xmlrpclib.Fault) as cm:
            xmlrpclib.loads(response.content)
        self.assertEqual(cm.exception.faultCode, 400)
        self.assertIn("does not serve", cm.exception.faultString)

    def test_xmlrpc_common_wrong_password_gives_no_membership_oracle(self):
        """A wrong password answers ``False`` for member and non-member alike.

        The gate reads membership only for a uid core has verified, so a caller
        without the password cannot tell members from non-members. ``version``
        stays open to anyone.
        """
        db = self.env.cr.dbname
        wrong = "definitely-not-the-password"  # nosec B105 - test fixture
        with patch.object(auth, "log_mcp_group_denied") as denied:
            for login in (self.nogroup_login, self.ingroup_login):
                response = self._xmlrpc_common("authenticate", db, login, wrong, {})
                self.assertIs(xmlrpclib.loads(response.content)[0][0], False)
        denied.assert_not_called()

        version = xmlrpclib.loads(self._xmlrpc_common("version").content)[0][0]
        self.assertIn("server_version", version)

    # ------------------------------------------------------------------
    # System administrators pass without an explicit grant
    # ------------------------------------------------------------------
    def test_system_admin_without_explicit_grant_passes(self):
        """base.group_system implies the MCP group, so admins never need a grant.

        Pins the implication chain group_system -> group_mcp_admin ->
        group_mcp_user that every other positive control sets up explicitly
        via grant_mcp_access.
        """
        unique_id = str(int(time.time() * 1000))[-6:]
        admin = create_test_user(
            self.env,
            "MCP Sysadmin",
            f"mcp_sysadmin_{unique_id}",
            email=f"mcp_sysadmin_{unique_id}@example.com",
            **{
                users_groups_field(self.env): [
                    (4, self.env.ref("base.group_system").id)
                ]
            },
        )
        # Reached only by implication: group_system is the only group assigned
        # explicitly; the MCP membership arrives through the implied chain,
        # which core materializes into the user's groups on write.
        groups = admin[users_groups_field(self.env)]
        self.assertIn(self.env.ref("base.group_system"), groups)
        self.assertIn(self.env.ref("mcp_server.group_mcp_user"), groups)
        key = self._mint_key(admin, "Sysadmin Key")
        self.assertEqual(self._post_rpc(key).status_code, 200)

    # ------------------------------------------------------------------
    # OAuth authorize (consent screen) and token grants
    # ------------------------------------------------------------------
    def test_consent_refused_for_non_member(self):
        """The consent screen shows a clear error to a non-member (no code).

        Failing at consent time -- not only at /mcp use time -- turns
        "connected, then every call 403s" into an explanation the resource
        owner can act on, and no dead token is ever minted.
        """
        client_id = self._register_client()
        self.authenticate(self.nogroup_login, self.password)
        params = self._authorize_params(
            client_id, _code_challenge(secrets.token_urlsafe(48))
        )
        response = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(response.status_code, 400, response.text[:500])
        self.assertIn("not authorized to use the MCP", response.text)

    def test_consent_error_survives_a_context_parameter(self):
        """``?context=x`` on authorize does not turn the consent error into a 500.

        Odoo's ``_()`` reads a local named ``kwargs`` as a dict holding a
        ``context``; the handler bound the request parameters to that name.
        """
        client_id = self._register_client()
        self.authenticate(self.nogroup_login, self.password)
        params = self._authorize_params(
            client_id, _code_challenge(secrets.token_urlsafe(48))
        )
        params["context"] = "x"
        response = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(response.status_code, 400, response.text[:500])
        self.assertIn("not authorized to use the MCP", response.text)

    def test_consent_reached_by_member(self):
        """A group member still reaches the consent screen (positive control)."""
        client_id = self._register_client()
        self.authenticate(self.ingroup_login, self.password)
        params = self._authorize_params(
            client_id, _code_challenge(secrets.token_urlsafe(48))
        )
        response = self.url_open("/mcp/oauth/authorize", params=params)
        self.assertEqual(response.status_code, 200, response.text[:500])
        self.assertIn("csrf_token", response.text)

    def test_code_exchange_after_group_removal_fails(self):
        """A code consented while a member dies at exchange after removal.

        The auth-code grant re-checks membership when binding the user
        (mirroring the archived-account check), so the window between consent
        and exchange cannot be used to smuggle out a token.
        """
        client_id = self._register_client()
        self.authenticate(self.ingroup_login, self.password)
        verifier = secrets.token_urlsafe(48)
        code = self._approve_consent(
            self._authorize_params(client_id, _code_challenge(verifier))
        )
        self._remove_mcp_group(self.user_ingroup)

        response = self._exchange_code(client_id, code, verifier)
        self.assertEqual(response.status_code, 400, response.text[:300])
        self.assertEqual(response.json().get("error"), "invalid_grant")

    def test_refresh_after_group_removal_fails(self):
        """An outstanding refresh token dies cleanly after group removal.

        The refresh grant re-checks membership, so the rotation cannot mint an
        access token that would only 403 at /mcp anyway.
        """
        client_id = self._register_client()
        self.authenticate(self.ingroup_login, self.password)
        verifier = secrets.token_urlsafe(48)
        code = self._approve_consent(
            self._authorize_params(client_id, _code_challenge(verifier))
        )
        exchange = self._exchange_code(client_id, code, verifier)
        self.assertEqual(exchange.status_code, 200, exchange.text[:300])
        refresh_token = exchange.json()["refresh_token"]

        self._remove_mcp_group(self.user_ingroup)

        response = self.url_open(
            "/mcp/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 400, response.text[:300])
        payload = response.json()
        # Authlib's refresh grant maps a refused user to invalid_request (only
        # the auth-code grant raises invalid_grant); the contract asserted here
        # is the refusal itself -- an RFC 6749 error and no token minted.
        self.assertIn(payload.get("error"), ("invalid_request", "invalid_grant"))
        self.assertNotIn("access_token", payload)
