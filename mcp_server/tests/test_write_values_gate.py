"""The MCP configuration also applies to the models that a write reaches.

An x2many command in the values needs its operation on the comodel, and a
write to ``ir.attachment`` needs write on the owner model. Both doors apply
the rule: the native write tools and the legacy XML-RPC proxy. Values in a
shape the gate does not read are refused, and the proxy refuses structured
defaults in the context. The write tools also turn a many2one given as
``[id, "name"]`` into the id, and ``resources/read`` refuses a value above its
size cap.
"""

import base64
import time
import xmlrpc.client as xmlrpclib  # nosec
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import common, tagged

from ..controllers import api, utils
from ..models import mcp_mixin
from ..tools.uri_schema import build_attachment_uri, build_field_uri, parse_field_uri
from .test_helpers import (
    create_test_config_settings,
    create_test_user,
    grant_mcp_access,
)


def _set_models(env, **models):
    """Give each model exactly the listed MCP operations; ``None`` disables it."""
    for name, operations in models.items():
        model_id = env["ir.model"]._get(name.replace("__", ".")).id
        record = (
            env["mcp.enabled.model"]
            .sudo()
            .with_context(active_test=False)
            .search([("model_id", "=", model_id)], limit=1)
        )
        values = {"active": operations is not None}
        for operation in ("read", "create", "write", "unlink"):
            values[f"allow_{operation}"] = operation in (operations or ())
        if record:
            record.write(values)
        elif operations is not None:
            env["mcp.enabled.model"].sudo().create(dict(values, model_id=model_id))
    env.flush_all()
    utils.clear_mcp_caches()


@tagged("much_unit", "post_install", "-at_install")
class TestWriteValuesGate(common.TransactionCase):
    """Native write tools: nested commands, attachment owners, many2one pairs."""

    def setUp(self):
        super().setUp()
        patcher = patch(
            "odoo.addons.mcp_server.controllers.utils.is_mcp_enabled",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        _set_models(
            self.env,
            res__partner=("read", "create", "write", "unlink"),
            res__partner__category=None,
            res__users=("read",),
            res__country=("read",),
        )
        self.tools = self.env["mcp.mixin"].with_user(self.env.ref("base.user_admin"))
        self.partner = self.env["res.partner"].create({"name": "Gate Partner"})
        self.tag = self.env["res.partner.category"].create({"name": "Gate Tag"})
        self.fr = self.env.ref("base.fr")

    def _operations(self, values):
        return list(utils.iter_nested_operations(self.env["res.partner"], values))

    def _allow_context_keys(self, *keys):
        """Let the proxy accept these context keys, as the setting does."""
        patcher = patch.object(
            type(self.env["mcp.enabled.model"]),
            "_get_proxy_context_keys",
            return_value=keys,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_command_operations_follow_core(self):
        bank, partner, tag = "res.partner.bank", "res.partner", "res.partner.category"
        cases = [
            ({"bank_ids": [[0, 0, {}]]}, [(bank, "create", "bank_ids")]),
            ({"bank_ids": [[1, 1, {}]]}, [(bank, "write", "bank_ids")]),
            ({"bank_ids": [[2, 1]]}, [(bank, "unlink", "bank_ids")]),
            # The inverse of bank_ids cascades: removing a line deletes it.
            ({"bank_ids": [[3, 1]]}, [(bank, "unlink", "bank_ids")]),
            ({"bank_ids": False}, [(bank, "unlink", "bank_ids")]),
            (
                {"bank_ids": [[6, 0, [1]]]},
                [(bank, "write", "bank_ids"), (bank, "unlink", "bank_ids")],
            ),
            # The inverse of child_ids does not cascade: removing writes it.
            ({"child_ids": [[3, 1]]}, [(partner, "write", "child_ids")]),
            ({"child_ids": [[4, 1]]}, [(partner, "write", "child_ids")]),
            # Links of a many2many change only the relation table.
            ({"category_id": [[4, 1], [3, 1], [5], [6, 0, [1]]]}, []),
            ({"category_id": [1, 2]}, []),
            ({"category_id": [[2, 1]]}, [(tag, "unlink", "category_id")]),
            # Core compares the code with ==, so 2.0 deletes too.
            ({"category_id": [[2.0, 1]]}, [(tag, "unlink", "category_id")]),
            (
                {"child_ids": [[0, 0, {"category_id": [[0, 0, {"name": "x"}]]}]]},
                [
                    (partner, "create", "child_ids"),
                    (tag, "create", "child_ids.category_id"),
                ],
            ),
            ({"name": "x", "country_id": 1}, []),
        ]
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(self._operations(values), expected)

    def test_nested_create_needs_create_on_the_comodel(self):
        values = {"name": "Nested", "category_id": [[0, 0, {"name": "New Tag"}]]}
        with self.assertRaisesRegex(
            AccessError,
            r"Operation 'create' is not allowed on model 'res.partner.category' "
            r"via MCP \(field 'category_id'\)",
        ):
            self.tools.create_record("res.partner", values)
        with self.assertRaisesRegex(AccessError, "'res.users'.*'user_ids'"):
            self.tools.create_record(
                "res.partner",
                {"name": "U", "user_ids": [[0, 0, {"name": "U", "login": "gate_u"}]]},
            )

        _set_models(self.env, res__partner__category=("read", "create"))
        self.tools.create_record("res.partner", values)
        self.assertTrue(
            self.env["res.partner.category"].search([("name", "=", "New Tag")])
        )

    def test_nested_update_and_delete_are_gated(self):
        for command, operation in (
            ([1, self.tag.id, {"name": "Renamed"}], "write"),
            ([2, self.tag.id], "unlink"),
        ):
            for call in (
                lambda c=command: self.tools.update_record(
                    "res.partner", self.partner.id, {"category_id": [c]}
                ),
                lambda c=command: self.tools.update_records(
                    "res.partner",
                    updates=[{"id": self.partner.id, "values": {"category_id": [c]}}],
                ),
            ):
                with self.subTest(command=command), self.assertRaisesRegex(
                    AccessError, f"Operation '{operation}'"
                ):
                    call()
        self.assertEqual(self.tag.name, "Gate Tag")

    def test_linking_existing_rows_needs_no_comodel_entry(self):
        self.tools.update_record(
            "res.partner", self.partner.id, {"category_id": [[4, self.tag.id]]}
        )
        self.assertEqual(self.partner.category_id, self.tag)
        self.tools.update_records(
            "res.partner", [self.partner.id], {"category_id": [[6, 0, []]]}
        )
        self.assertFalse(self.partner.category_id)

    def test_many2one_pair_is_written_as_its_id(self):
        for value in ([self.fr.id, "France"], [self.fr.id], {"id": self.fr.id}):
            self.partner.country_id = False
            with self.subTest(value=value):
                self.tools.update_record(
                    "res.partner", self.partner.id, {"country_id": value}
                )
                self.assertEqual(self.partner.country_id, self.fr)

        result = self.tools.create_record(
            "res.partner",
            {
                "name": "Pair",
                "country_id": [self.fr.id, "France"],
                "child_ids": [[0, 0, {"name": "Child", "country_id": [self.fr.id]}]],
            },
        )
        created = self.env["res.partner"].browse(
            result["structuredContent"]["record"]["id"]
        )
        self.assertEqual(created.country_id, self.fr)
        self.assertEqual(created.child_ids.country_id, self.fr)

    def test_other_many2one_shapes_are_refused(self):
        for value in ("France", [1, 2, 3], {"name": "France"}, True):
            with self.subTest(value=value), self.assertRaisesRegex(
                UserError, "takes a record id"
            ):
                self.tools.update_record(
                    "res.partner", self.partner.id, {"country_id": value}
                )

    def test_empty_many2one_forms_follow_core(self):
        for value in ("", []):
            self.partner.country_id = self.fr
            with self.subTest(value=value):
                self.tools.update_record(
                    "res.partner", self.partner.id, {"country_id": value}
                )
                self.assertFalse(self.partner.country_id)
        # A record with an empty name reads back as [id, false].
        self.tools.update_record(
            "res.partner", self.partner.id, {"country_id": [self.fr.id, False]}
        )
        self.assertEqual(self.partner.country_id, self.fr)

    def test_values_that_are_not_a_dict_are_refused(self):
        pairs = [["name", "C"], ["category_id", [[0, 0, {"name": "Pair Tag"}]]]]
        calls = (
            lambda: self.tools.update_record(
                "res.partner", self.partner.id, {"child_ids": [[0, 0, pairs]]}
            ),
            lambda: self.tools.update_record(
                "res.partner", self.partner.id, {"child_ids": [[1, 1, pairs]]}
            ),
            lambda: self.tools.create_records(
                "res.partner", [{"name": "B", "child_ids": [[0, 0, pairs]]}]
            ),
            lambda: self.tools.update_records(
                "res.partner", [self.partner.id], {"child_ids": [[0, 0, pairs]]}
            ),
        )
        for index, call in enumerate(calls):
            with self.subTest(call=index), self.assertRaisesRegex(
                UserError, "must be an object"
            ):
                call()
        self.assertFalse(
            self.env["res.partner.category"].search([("name", "=", "Pair Tag")])
        )

    def test_command_lists_hold_only_commands_or_ids(self):
        tag = self.tag.id
        for value in (
            [[5], {"name": "Dict Tag"}],
            [{"name": "Dict Tag"}],
            [[4, tag], tag],
            [tag, [0, 0, {"name": "Dict Tag"}]],
            [[]],
            {"name": "Dict Tag"},
            tag,
        ):
            values = {"name": "Dict", "category_id": value}
            with self.subTest(value=value):
                with self.assertRaisesRegex(UserError, "list of commands"):
                    self.tools.create_record("res.partner", values)
                with self.assertRaises(utils.UnreadableWriteValues):
                    utils.legacy_write_denial(
                        self.env, "res.partner", "create", [values], {}
                    )
        self.assertFalse(
            self.env["res.partner.category"].search([("name", "=", "Dict Tag")])
        )
        self.assertEqual(self._operations({"category_id": []}), [])
        # Core cannot write a float id: its browse() raises TypeError.
        for ids in ([float(tag)], [True]):
            with self.subTest(ids=ids), self.assertRaises(utils.UnreadableWriteValues):
                self._operations({"category_id": ids})

    def test_a_new_record_in_a_command_list_is_refused(self):
        """Core reads a dict in a command list as a new record of the comodel.

        A many2many that Odoo computes and writes back, such as
        ``unrestricted_attachment_ids`` of a mail, then saves that record.
        """
        _set_models(self.env, mail__mail=("read", "create"), ir__attachment=None)
        for value in ([{"name": "gate_dict.txt"}], [[5], {"name": "gate_dict.txt"}]):
            with self.subTest(value=value), self.assertRaisesRegex(
                UserError, "list of commands"
            ):
                self.tools.create_record(
                    "mail.mail",
                    {"subject": "Dict", "unrestricted_attachment_ids": value},
                )
        self.assertFalse(
            self.env["ir.attachment"].search([("name", "=", "gate_dict.txt")])
        )

    def test_a_computed_many2many_needs_write_on_the_linked_records(self):
        field = "unrestricted_attachment_ids"
        attachment = self.env["ir.attachment"].create({"name": "linked.txt"})
        mail = self.env["mail.mail"]
        for command in ([4, attachment.id], [3, attachment.id], [5], [6, 0, [1]]):
            with self.subTest(command=command):
                self.assertEqual(
                    list(utils.iter_nested_operations(mail, {field: [command]})),
                    [("ir.attachment", "write", field)],
                )
        # On create, core starts the field empty, so the inverse also removes the
        # rows that the field computes: an implicit clear first.
        self.assertEqual(
            list(utils.iter_nested_operations(mail, {field: [[5]]}, creating=True)),
            [("ir.attachment", "write", field)] * 2,
        )
        _set_models(self.env, mail__mail=("read", "create"), ir__attachment=None)
        values = {"subject": "Link", field: [[6, 0, [attachment.id]]]}
        with self.assertRaisesRegex(AccessError, "'ir.attachment'"):
            self.tools.create_record("mail.mail", values)
        _set_models(self.env, ir__attachment=("read", "write"))
        on_users = self.env["ir.attachment"].create(
            {
                "name": "on_users.txt",
                "res_model": "res.users",
                "res_id": self.env.ref("base.user_admin").id,
            }
        )
        values[field] = [[6, 0, [on_users.id]]]
        with self.assertRaisesRegex(AccessError, "'res.users'"):
            self.tools.create_record("mail.mail", values)

    def test_related_fields_follow_the_field_that_core_writes(self):
        """An ``_inherits`` field writes the field of the parent record."""
        users, bank = self.env["res.users"], "res.partner.bank"
        cases = [
            # Links of the partner's many2many change only the relation table.
            ({"category_id": [[4, 1], [6, 0, [1]]]}, False, []),
            # The partner deletes the bank accounts that it removes.
            ({"bank_ids": [[3, 1]]}, False, [(bank, "unlink", "bank_ids")]),
            # A new user creates its partner, which removes nothing.
            ({"bank_ids": [[5]]}, True, []),
            # A new user of an existing partner writes that partner.
            (
                {"partner_id": 1, "bank_ids": [[5]]},
                True,
                [(bank, "unlink", "bank_ids")],
            ),
            ({"partner_id": 1, "category_id": [[6, 0, [1]]]}, True, []),
        ]
        for values, creating, expected in cases:
            with self.subTest(values=values, creating=creating):
                self.assertEqual(
                    list(
                        utils.iter_nested_operations(users, values, creating=creating)
                    ),
                    expected,
                )
        _set_models(self.env, res__users=("read", "write"))
        user = create_test_user(self.env, "Gate Tags", "gate_tags")
        self.tools.update_record(
            "res.users", user.id, {"category_id": [[4, self.tag.id]]}
        )
        self.assertEqual(user.category_id, self.tag)

    def test_core_can_name_the_parent_of_a_new_record(self):
        """Core writes an existing parent when a line or the context names it.

        A new one2many line gets the inverse of the record. For a user under a
        partner that inverse is the parent link, so the user writes the partner.
        On the legacy proxy, a ``default_`` key of the context names it too.
        """
        bank = "res.partner.bank"
        line = {"name": "U", "login": "gate_line", "bank_ids": [[5]]}
        for creating in (False, True):
            with self.subTest(creating=creating):
                self.assertEqual(
                    list(
                        utils.iter_nested_operations(
                            self.env["res.partner"],
                            {"user_ids": [[0, 0, line]]},
                            creating=creating,
                        )
                    ),
                    [
                        ("res.users", "create", "user_ids"),
                        (bank, "unlink", "user_ids.bank_ids"),
                    ],
                )
        _set_models(self.env, res__partner__bank=("read", "write"))
        self._allow_context_keys("default_partner_id", "default_bank_ids")
        values = {"name": "U", "login": "gate_default", "bank_ids": [[5]]}
        self.assertIsNone(
            utils.legacy_write_denial(self.env, "res.users", "create", [values], {})
        )
        denied = utils.legacy_write_denial(
            self.env,
            "res.users",
            "create",
            [values],
            {"context": {"default_partner_id": self.partner.id}},
        )
        self.assertIn("operation 'unlink' on model 'res.partner.bank'", denied)
        # A false default names no parent: core creates a new partner.
        self.assertIsNone(
            utils.legacy_write_denial(
                self.env,
                "res.users",
                "create",
                [values],
                {"context": {"default_partner_id": False}},
            )
        )
        # Core fills a missing x2many field from its default: False clears it.
        # name_create and a copy without a default create a record too.
        user = create_test_user(self.env, "Gate Copy", "gate_copy")
        context = {
            "context": {
                "default_partner_id": self.partner.id,
                "default_bank_ids": False,
            }
        }
        for method, args in (
            ("create", [{"name": "U", "login": "gate_default_banks"}]),
            ("name_create", ["U"]),
            ("copy", [user.id]),
        ):
            with self.subTest(method=method):
                self.assertIn(
                    "operation 'unlink' on model 'res.partner.bank'",
                    utils.legacy_write_denial(
                        self.env, "res.users", method, args, context
                    ),
                )

    def test_a_field_with_an_inverse_is_replaced_on_create(self):
        """On create, core starts such a field empty: its inverse removes rows.

        Stand-in fields: no model of the test database has a computed one2many
        with an inverse method or a writable related one2many.
        """
        bank = "res.partner.bank"
        lines = SimpleNamespace(
            type="one2many",
            comodel_name=bank,
            inverse_name="partner_id",
            store=False,
            inverse="_inverse_lines",
            related_field=None,
            inherited=False,
        )
        banks = SimpleNamespace(
            type="one2many",
            comodel_name=bank,
            inverse_name="partner_id",
            store=False,
            inverse="_inverse_related",
            related_field=self.env["res.partner"]._fields["bank_ids"],
            inherited=False,
        )
        model = SimpleNamespace(
            _name="mcp.stand.in",
            _fields={"lines": lines, "banks": banks},
            _inherits={},
            env=self.env,
        )
        write, unlink = (bank, "write", "lines"), (bank, "unlink", "lines")
        cases = [
            ({"lines": [[0, 0, {}]]}, True, [write, unlink, (bank, "create", "lines")]),
            ({"lines": []}, True, [write, unlink]),
            # The inverse method can delete the lines that it removes.
            ({"lines": [[3, 1]]}, False, [write, unlink]),
            (
                {"banks": [[6, 0, [1]]]},
                True,
                [
                    (bank, "unlink", "banks"),
                    (bank, "write", "banks"),
                    (bank, "unlink", "banks"),
                ],
            ),
            ({"banks": [[4, 1]]}, False, [(bank, "write", "banks")]),
        ]
        for values, creating, expected in cases:
            with self.subTest(values=values, creating=creating):
                self.assertEqual(
                    list(
                        utils.iter_nested_operations(model, values, creating=creating)
                    ),
                    expected,
                )

    def test_batch_tools_apply_the_gate(self):
        with self.assertRaisesRegex(AccessError, "'res.users'.*'user_ids'"):
            self.tools.create_records(
                "res.partner",
                [{"name": "B", "user_ids": [[0, 0, {"name": "B", "login": "gate_b"}]]}],
            )
        with self.assertRaisesRegex(AccessError, "Operation 'unlink'"):
            self.tools.update_records(
                "res.partner", [self.partner.id], {"category_id": [[2, self.tag.id]]}
            )
        self.assertTrue(self.tag.exists())

    def test_creating_removes_no_line(self):
        bank = "res.partner.bank"
        creating = [
            ({"bank_ids": False}, []),
            ({"bank_ids": [[5]]}, []),
            ({"bank_ids": [[6, 0, []]]}, []),
            ({"bank_ids": [[6, 0, [1]]]}, [(bank, "write", "bank_ids")]),
            # Unlink (3) removes the line even while the record is created.
            ({"bank_ids": [[3, 1]]}, [(bank, "unlink", "bank_ids")]),
        ]
        for values, expected in creating:
            with self.subTest(values=values):
                self.assertEqual(
                    list(
                        utils.iter_nested_operations(
                            self.env["res.partner"], values, creating=True
                        )
                    ),
                    expected,
                )
        # The values of a create command create a record too.
        self.assertEqual(
            self._operations({"child_ids": [[0, 0, {"bank_ids": False}]]}),
            [("res.partner", "create", "child_ids")],
        )
        self.tools.create_record("res.partner", {"name": "No Banks", "bank_ids": None})
        with self.assertRaisesRegex(AccessError, "'unlink'.*'res.partner.bank'"):
            self.tools.update_record("res.partner", self.partner.id, {"bank_ids": None})

    def test_nested_attachments_need_write_on_the_owner(self):
        _set_models(
            self.env,
            mail__message=("read", "create", "write"),
            ir__attachment=("read", "create", "write", "unlink"),
        )
        admin = self.env.ref("base.user_admin")
        message = self.env["mail.message"].create(
            {"body": "gate", "model": "res.partner", "res_id": self.partner.id}
        )
        on_users = self.env["ir.attachment"].create(
            {"name": "u.txt", "res_model": "res.users", "res_id": admin.id}
        )
        refused = (
            r"Operation 'write' is not allowed on model 'res.users' via MCP "
            r"\(field 'attachment_ids.res_model'\)"
        )
        for command in (
            [0, 0, {"name": "n.txt", "res_model": "res.users", "res_id": admin.id}],
            [1, on_users.id, {"name": "v.txt"}],
            [2, on_users.id],
        ):
            with self.subTest(command=command), self.assertRaisesRegex(
                AccessError, refused
            ):
                self.tools.update_record(
                    "mail.message", message.id, {"attachment_ids": [command]}
                )
        self.assertEqual(on_users.name, "u.txt")
        # Linking an attachment to the message does not change the attachment.
        self.tools.update_record(
            "mail.message", message.id, {"attachment_ids": [[4, on_users.id]]}
        )
        self.tools.update_record(
            "mail.message",
            message.id,
            {"attachment_ids": [[0, 0, {"name": "p.txt", "res_model": "res.partner"}]]},
        )

    def test_attachment_writes_need_write_on_the_owner(self):
        _set_models(self.env, ir__attachment=("read", "create", "write", "unlink"))
        data = base64.b64encode(b"gate").decode()
        refused = r"Operation 'write' is not allowed on model 'res.users' via MCP"
        with self.assertRaisesRegex(AccessError, refused):
            self.tools.create_record(
                "ir.attachment",
                {
                    "name": "a.txt",
                    "res_model": "res.users",
                    "res_id": self.env.ref("base.user_admin").id,
                    "datas": data,
                },
            )

        result = self.tools.create_record(
            "ir.attachment",
            {
                "name": "a.txt",
                "res_model": "res.partner",
                "res_id": self.partner.id,
                "datas": data,
            },
        )
        attachment_id = result["structuredContent"]["record"]["id"]
        with self.assertRaisesRegex(AccessError, refused):
            self.tools.update_record(
                "ir.attachment", attachment_id, {"res_model": "res.users"}
            )

        on_users = self.env["ir.attachment"].create(
            {
                "name": "u.txt",
                "res_model": "res.users",
                "res_id": self.env.ref("base.user_admin").id,
                "datas": data,
            }
        )
        for call in (
            lambda: self.tools.update_record(
                "ir.attachment", on_users.id, {"name": "v.txt"}
            ),
            lambda: self.tools.delete_record("ir.attachment", on_users.id),
        ):
            with self.assertRaisesRegex(AccessError, refused):
                call()

        # An attachment without an owner needs only the ir.attachment entry.
        self.tools.create_record("ir.attachment", {"name": "free.txt", "datas": data})

    def test_module_models_refuse_method_calls(self):
        for model, method in (
            ("ir.module.module", "button_immediate_uninstall"),
            ("base.module.upgrade", "upgrade_module"),
        ):
            with self.subTest(model=model), self.assertRaisesRegex(
                AccessError, "not permitted via MCP"
            ):
                self.tools._validate_method_call(model, self.env[model], method)

    def test_resources_read_has_a_size_cap(self):
        _set_models(self.env, ir__attachment=("read",))
        attachment = self.env["ir.attachment"].create(
            {
                "name": "big.bin",
                "res_model": "res.partner",
                "res_id": self.partner.id,
                "raw": bytes(range(10)),
            }
        )
        field_uri = build_field_uri("ir.attachment", attachment.id, "datas")
        with patch.object(mcp_mixin, "MAX_RESOURCE_BYTES", 4):
            with self.assertRaisesRegex(UserError, "limit of resources/read"):
                self.tools._read_attachment(
                    build_attachment_uri(attachment.id), attachment.id
                )
            with self.assertRaisesRegex(UserError, "limit of resources/read"):
                self.tools._read_record_field(field_uri, parse_field_uri(field_uri))
        entry = self.tools._read_attachment(
            build_attachment_uri(attachment.id), attachment.id
        )
        self.assertEqual(base64.b64decode(entry["blob"]), bytes(range(10)))

    def test_export_data_is_not_mapped(self):
        self.assertIsNone(utils.map_method_to_operation("export_data"))

    def test_legacy_operands(self):
        env = self.env
        cases = [
            ("create", [{"name": "x"}], {}, [{"name": "x"}]),
            (
                "create",
                [[{"name": "x"}, {"name": "y"}]],
                {},
                [{"name": "x"}, {"name": "y"}],
            ),
            ("create", [], {"vals_list": {"name": "x"}}, [{"name": "x"}]),
            ("write", [[1], {"name": "x"}], {}, [{"name": "x"}]),
            ("write", [[1]], {"vals": {"name": "x"}}, [{"name": "x"}]),
            ("copy", [1, {"name": "x"}], {}, [{"name": "x"}]),
            ("unlink", [[1]], {}, []),
            ("search_read", [[]], {}, []),
        ]
        for method, args, kwargs, expected in cases:
            with self.subTest(method=method, args=args, kwargs=kwargs):
                records, values_list = utils.legacy_write_operands(
                    env, "res.partner", method, args, kwargs
                )
                self.assertEqual(values_list, expected)
                self.assertFalse(records)

    def test_legacy_call_shapes_the_gate_cannot_read(self):
        env = self.env
        attachment = env["ir.attachment"].create({"name": "s.txt"})
        cases = [
            ("res.partner", "write", [[1], {"name": "x"}], [["vals", {}]]),
            ("res.partner", "write", [[1]], {"values": {"name": "x"}}),
            ("res.partner", "create", [[{"name": "x"}, [["name", "y"]]]], {}),
            ("res.partner", "write", [[1], [["name", "x"]]], {}),
            ("res.partner", "copy", [1, [["name", "x"]]], {}),
            ("res.partner", "create", [{"name": "x"}], {"context": [["a", 1]]}),
            ("ir.attachment", "write", [[float(attachment.id)], {"name": "y"}], {}),
            ("ir.attachment", "unlink", str(attachment.id), {}),
            ("ir.attachment", "write", str(attachment.id), {"vals": {"name": "y"}}),
            ("res.partner", "write", {"1": {"name": "x"}}, {}),
            (
                "ir.attachment",
                "create",
                [{"name": "y", "res_model": ["res.users"]}],
                {},
            ),
        ]
        for model, method, args, kwargs in cases:
            with self.subTest(model=model, method=method, args=args, kwargs=kwargs):
                with self.assertRaises(utils.UnreadableWriteValues):
                    utils.legacy_write_denial(env, model, method, args, kwargs)

    def test_legacy_context_defaults(self):
        env = self.env
        _set_models(env, ir__attachment=("read", "create", "write"))
        self._allow_context_keys(
            "default_category_id", "default_type", "default_res_model", "default_res_id"
        )
        structured = {"default_category_id": [[0, 0, {"name": "Ctx Tag"}]]}
        for method, args in (
            ("create", [{"name": "x"}]),
            ("name_create", ["x"]),
            ("copy", [self.partner.id]),
            ("write", [[self.partner.id], {"child_ids": [[0, 0, {"name": "c"}]]}]),
        ):
            with self.subTest(method=method):
                self.assertIn(
                    "context key 'default_category_id'",
                    utils.legacy_write_denial(
                        env, "res.partner", method, args, {"context": structured}
                    ),
                )
        self.assertIsNone(
            utils.legacy_write_denial(
                env,
                "res.partner",
                "create",
                [{"name": "x"}],
                {"context": {"default_type": "contact", "lang": "en_US"}},
            )
        )
        owner = {"context": {"default_res_model": "res.partner", "default_res_id": 1}}
        self.assertIn(
            "context key 'default_res_model'",
            utils.legacy_write_denial(
                env, "ir.attachment", "create", [{"name": "a"}], owner
            ),
        )
        _set_models(env, mail__message=("read", "write"))
        message = env["mail.message"].create({"body": "ctx"})
        self.assertIn(
            "context key 'default_res_model'",
            utils.legacy_write_denial(
                env,
                "mail.message",
                "write",
                [[message.id], {"attachment_ids": [[0, 0, {"name": "a"}]]}],
                owner,
            ),
        )

    def test_legacy_context_keys_are_allow_listed(self):
        """A method that writes can send only the allowed context keys.

        Core and standard modules read many context keys to skip a check. For
        example, ``_force_unlink`` skips the delete guards of ``unlink``, and
        ``leave_fast_create`` the approval of time off. A ``default_`` key needs
        the setting too: with ``default_state``, core creates a posted journal
        entry. The setting ``mcp_server.proxy_context_keys`` allows more keys,
        but never ``_import_current_module``: with it, core's create writes XML
        ids.
        """
        pid = self.partner.id
        calls = (
            ("create", [{"name": "x", "id": "base.public_user"}], {}),
            ("write", [[pid], {"name": "x"}], {}),
            ("copy", [pid], {}),
            ("name_create", ["x"], {}),
            ("unlink", [[pid]], {}),
            ("message_post", [[pid]], {"body": "x", "id": "base.public_user"}),
        )

        def denial(method, args, kwargs, context):
            return utils.legacy_write_denial(
                self.env, "res.partner", method, args, dict(kwargs, context=context)
            )

        refused = (
            "_import_current_module",
            "_force_unlink",
            "force_delete",
            "check_move_validity",
            "leave_fast_create",
            "default_state",
        )
        for key in refused:
            for method, args, kwargs in calls:
                with self.subTest(key=key, method=method):
                    self.assertIn(
                        f"context key '{key}'",
                        denial(method, args, kwargs, {key: True}),
                    )
        allowed = dict.fromkeys(utils._PROXY_CONTEXT_KEYS, False)
        allowed.update(lang="en_US", uid=self.env.uid)
        for method, args, kwargs in calls[1:]:
            with self.subTest(method=method, context="built-in keys"):
                self.assertNotIn(
                    "context key", denial(method, args, kwargs, allowed) or ""
                )
        create_test_config_settings(
            self.env,
            mcp_proxy_context_keys=" check_move_validity ,_import_current_module,,"
            " default_comment",
        ).execute()
        for method, args, kwargs in calls[1:]:
            with self.subTest(method=method, context="setting"):
                self.assertNotIn(
                    "context key",
                    denial(
                        method,
                        args,
                        kwargs,
                        {"check_move_validity": False, "default_comment": "x"},
                    )
                    or "",
                )
                self.assertRegex(
                    denial(method, args, kwargs, {"force_delete": True}),
                    "context key 'force_delete'.*Extra Context Keys for XML-RPC Writes",
                )
                self.assertIn(
                    "context key '_import_current_module': it writes XML ids",
                    denial(method, args, kwargs, {"_import_current_module": "x"}),
                )
        # Core also reads a list of pairs as a context or as keyword arguments.
        for method in ("message_post", "toggle_active", "action_archive", "write"):
            for kwargs in (
                {"context": [["_import_current_module", "base"]]},
                [["context", {"_import_current_module": "base"}]],
            ):
                with self.subTest(method=method, kwargs=kwargs), self.assertRaises(
                    utils.UnreadableWriteValues
                ):
                    utils.legacy_write_denial(
                        self.env, "res.partner", method, [[pid], {}], kwargs
                    )
        # A read writes nothing.
        for context in ({"_force_unlink": True}, [["_force_unlink", True]]):
            with self.subTest(context=context):
                self.assertIsNone(
                    utils.legacy_write_denial(
                        self.env,
                        "res.partner",
                        "search_read",
                        [[]],
                        {"context": context},
                    )
                )

    def test_legacy_owner_name_is_one_line(self):
        denied = utils.legacy_write_denial(
            self.env,
            "ir.attachment",
            "create",
            [{"name": "a", "res_model": "res.users\nforged line"}],
            {},
        )
        self.assertIn("Access denied by MCP", denied)
        self.assertNotIn("\n", denied)

    def test_legacy_attachment_calls_check_the_stored_owner(self):
        _set_models(self.env, ir__attachment=("read", "create", "write", "unlink"))
        on_users = self.env["ir.attachment"].create(
            {
                "name": "lu.txt",
                "res_model": "res.users",
                "res_id": self.env.ref("base.user_admin").id,
            }
        )
        for method, args in (
            ("write", [[on_users.id], {"name": "x"}]),
            ("unlink", [[on_users.id]]),
            ("copy", [on_users.id]),
        ):
            with self.subTest(method=method):
                self.assertIn(
                    "operation 'write' on model 'res.users' (field 'res_model')",
                    utils.legacy_write_denial(
                        self.env, "ir.attachment", method, args, {}
                    ),
                )

    def test_legacy_call_shapes_core_accepts(self):
        for method, args, kwargs in (
            ("create", [{"name": "x"}], []),
            ("create", [{"name": "x"}], None),
            ("copy", [self.partner.id, False], {}),
            ("copy", [self.partner.id], {"default": None}),
        ):
            with self.subTest(method=method, args=args, kwargs=kwargs):
                self.assertIsNone(
                    utils.legacy_write_denial(
                        self.env, "res.partner", method, args, kwargs
                    )
                )

    def test_model_names_are_logged_on_one_line(self):
        with self.assertLogs(utils._logger, "WARNING") as logs:
            self.assertFalse(
                utils.check_model_operation_allowed(
                    self.env, "res.users\nforged line", "write"
                )
            )
        self.assertNotIn("\n", "".join(logs.output))


@tagged("much_unit", "post_install", "-at_install")
class TestLegacyWriteValuesGate(common.HttpCase):
    """The legacy XML-RPC proxy applies the same rule.

    On Odoo 17 the HttpCase test cursor is not read-only, so a call that passes
    the gate returns its result.
    """

    def setUp(self):
        super().setUp()
        self.password = "gate_pw"  # nosec B105 - test fixture credential
        unique_id = str(int(time.time() * 1000))[-6:]
        self.user = create_test_user(
            self.env,
            "Legacy Gate User",
            f"legacy_gate_{unique_id}",
            password=self.password,
            group_ids=[
                (
                    6,
                    0,
                    [
                        self.env.ref("base.group_user").id,
                        self.env.ref("base.group_partner_manager").id,
                    ],
                )
            ],
        )
        grant_mcp_access(self.user)
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("mcp_server.enabled", "True")
        params.set_param("mcp_server.enable_logging", "True")
        _set_models(
            self.env,
            res__partner=("read", "create", "write"),
            res__partner__category=None,
        )

    def _execute(
        self, method, args, kwargs=None, model="res.partner", uid=None, password=None
    ):
        response = self.url_open(
            "/mcp/xmlrpc/object",
            data=xmlrpclib.dumps(
                (
                    self.env.cr.dbname,
                    self.user.id if uid is None else uid,
                    self.password if password is None else password,
                    model,
                    method,
                    args,
                    {} if kwargs is None else kwargs,
                ),
                "execute_kw",
                allow_none=1,
            ),
            headers={"Content-Type": "text/xml"},
        )
        return xmlrpclib.loads(response.content)[0][0]

    def test_nested_command_is_refused(self):
        # HttpCase runs the route on a read-only cursor, where log_event writes
        # no row, so the test checks the call that writes it.
        with patch.object(
            type(self.env["mcp.log"]), "log_permission_denied", autospec=True
        ) as denied, self.assertRaises(xmlrpclib.Fault) as caught:
            self._execute(
                "create", [{"name": "L", "category_id": [[0, 0, {"name": "L Tag"}]]}]
            )
        self.assertEqual(caught.exception.faultCode, 403)
        self.assertIn(
            "operation 'create' on model 'res.partner.category' "
            "(field 'category_id')",
            caught.exception.faultString,
        )
        denied.assert_called_once()
        self.assertEqual(denied.call_args.kwargs["user_id"], self.user.id)
        self.assertEqual(denied.call_args.kwargs["endpoint"], "/mcp/xmlrpc/object")
        self.assertIn(
            "'res.partner.category'", denied.call_args.kwargs["error_message"]
        )
        # An empty array of keyword arguments is no keyword argument, as in core.
        for kwargs in ({}, []):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(self._execute("create", [{"name": "Plain"}], kwargs))

    def test_unproven_credentials_never_reach_the_gate(self):
        """The gate reads records, so it runs only after the password is proven."""
        _set_models(self.env, ir__attachment=("read", "write", "unlink"))
        admin = self.env.ref("base.user_admin")
        on_users = self.env["ir.attachment"].create(
            {"name": "cred.txt", "res_model": "res.users", "res_id": admin.id}
        )
        for uid, password in (
            (admin.id, ""),
            (admin.id, 12345),
            ("x", self.password),
            (float("inf"), self.password),
            (self.user.id, ""),
            # Passlib refuses to hash it, for an existing user only.
            (self.user.id, "x" * 5000),
        ):
            with self.subTest(uid=uid, password=password), self.assertRaises(
                xmlrpclib.Fault
            ) as caught:
                self._execute(
                    "unlink",
                    [[on_users.id]],
                    model="ir.attachment",
                    uid=uid,
                    password=password,
                )
            self.assertEqual(caught.exception.faultCode, 3)
            self.assertNotIn("res.users", caught.exception.faultString)
        self.assertTrue(on_users.exists())

    def test_a_computed_many2many_needs_write_on_the_linked_records(self):
        _set_models(self.env, mail__mail=("read", "create"), ir__attachment=None)
        attachment = self.env["ir.attachment"].create({"name": "legacy_link.txt"})
        with self.assertRaises(xmlrpclib.Fault) as caught:
            self._execute(
                "create",
                [
                    {
                        "subject": "Link",
                        "unrestricted_attachment_ids": [[6, 0, [attachment.id]]],
                    }
                ],
                model="mail.mail",
            )
        self.assertEqual(caught.exception.faultCode, 403)
        self.assertIn(
            "operation 'write' on model 'ir.attachment'", caught.exception.faultString
        )

    def test_call_shapes_and_context_defaults_are_refused(self):
        partner = self.env["res.partner"].create({"name": "Shape"})
        for args, kwargs, code, text in (
            ([[partner.id]], {"values": {"name": "x"}}, 400, "no keyword argument"),
            (
                [[partner.id], {"child_ids": [[0, 0, [["name", "c"]]]]}],
                {},
                400,
                "must be an object",
            ),
            (
                [[partner.id], {"name": "x"}],
                {"context": {"default_category_id": [[0, 0, {"name": "t"}]]}},
                403,
                "context key 'default_category_id'",
            ),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(
                xmlrpclib.Fault
            ) as caught:
                self._execute("write", args, kwargs)
            self.assertEqual(caught.exception.faultCode, code)
            self.assertIn(text, caught.exception.faultString)

    def test_value_error_keeps_its_text(self):
        for error in (
            ValueError("Invalid field 'no_such_field' on model 'res.partner'"),
            TypeError("write() got an unexpected keyword argument 'x'"),
        ):
            with self.subTest(error=type(error).__name__):
                with self.assertLogs(api._logger, "WARNING") as logs:
                    self.assertEqual(
                        api._fault_for_exception("MCPObjectController", error),
                        (api.RPC_FAULT_CODE_APPLICATION_ERROR, str(error)),
                    )
                self.assertIsNotNone(logs.records[0].exc_info)
                self.assertEqual(
                    api._audit_code_for(error), api.XMLRPC_FAULT_CODES["bad_request"]
                )

    def test_export_data_is_refused(self):
        with self.assertRaises(xmlrpclib.Fault) as caught:
            self._execute("export_data", [[1], ["name"]])
        self.assertEqual(caught.exception.faultCode, 403)
