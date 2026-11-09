# Offline tests for who may become a lead of @marketingagent67_bot. Run: python3 -m unittest discover -s tests
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import agent_lib as A  # noqa: E402


def state(leads=(7106984902, 6019684316), **settings):
    st = A._blank()
    st["leads"] = list(leads)
    st["lead_names"] = {str(c): f"lead{c}" for c in leads}
    st["settings"] = dict(settings)
    return st


def start_msg(uid, username=None, text="/start"):
    frm = {"id": uid, "first_name": "New", **({"username": username} if username else {})}
    return {"message_id": 1, "chat": {"id": uid, "type": "private"}, "from": frm, "text": text}


class MayRegister(unittest.TestCase):
    def test_open_registration_until_the_cap_without_a_list(self):
        self.assertTrue(A.may_register(state(leads=[1]), start_msg(5)))
        self.assertFalse(A.may_register(state(leads=list(range(A.MAX_LEADS))), start_msg(5)))

    def test_allowed_by_id(self):
        st = state(allowed_users=["5562302157"])
        self.assertTrue(A.may_register(st, start_msg(5562302157)))

    def test_allowed_by_username_any_case_with_or_without_at(self):
        st = state(allowed_users=["@Alice_Uz", "bob"])
        self.assertTrue(A.may_register(st, start_msg(11, "alice_uz")))
        self.assertTrue(A.may_register(st, start_msg(12, "Bob")))

    def test_list_closes_open_registration(self):
        st = state(leads=[1], allowed_users=["5562302157"])
        self.assertFalse(A.may_register(st, start_msg(99, "stranger")))

    def test_invite_code(self):
        st = state(invite_code="Abc123_x")
        self.assertTrue(A.may_register(st, start_msg(77, text="/start Abc123_x")))
        self.assertFalse(A.may_register(st, start_msg(78, text="/start wrong")))
        self.assertFalse(A.may_register(st, start_msg(79, text="/start")))


class Poll(unittest.TestCase):
    def run_poll(self, st, msg):
        upd = {"update_id": 500, "message": msg}
        with mock.patch.object(A, "tg", return_value={"result": [upd]}), \
                mock.patch.object(A, "_send_text") as send, mock.patch.object(A, "save_state"):
            acts = A.poll(st)
        return acts, send

    def test_third_lead_from_the_list_is_registered_and_greeted(self):
        st = state(allowed_users=["5562302157"])
        acts, send = self.run_poll(st, start_msg(5562302157, "newlead"))
        self.assertEqual(st["leads"], [7106984902, 6019684316, 5562302157])
        self.assertEqual(st["lead_names"]["5562302157"], A._name(start_msg(5562302157, "newlead")["from"]))
        self.assertEqual(acts[0]["type"], "registered")
        send.assert_called_once_with(5562302157, A.GREETING)

    def test_stranger_is_turned_away(self):
        st = state(allowed_users=["5562302157"])
        acts, send = self.run_poll(st, start_msg(424242, "stranger"))
        self.assertEqual(st["leads"], [7106984902, 6019684316])
        self.assertEqual(acts, [])
        self.assertNotEqual(send.call_args.args[1], A.GREETING)


class Merge(unittest.TestCase):
    def test_merge_keeps_every_lead(self):
        remote = state(leads=[1, 2])
        local = state(leads=[1, 2, 3, 4])
        self.assertEqual(A._merge(remote, local)["leads"], [1, 2, 3, 4])

    def test_removed_lead_stays_removed_after_merge(self):
        remote = state(leads=[1, 2, 3])
        local = state(leads=[1, 2, 3])
        A.remove_lead(local, "3")
        self.assertEqual(A._merge(remote, local)["leads"], [1, 2])

    def test_allowing_again_undoes_a_removal(self):
        st = state(leads=[1, 2, 3])
        A.remove_lead(st, "3")
        A.allow_users(st, ["3"])
        self.assertNotIn("3", st["settings"].get("removed_leads", []))


class Manage(unittest.TestCase):
    def test_allow_normalises_and_dedupes(self):
        st = state()
        A.allow_users(st, ["@Alice", "5562302157", "alice", "5562302157"])
        self.assertEqual(st["settings"]["allowed_users"], ["alice", "5562302157"])

    def test_remove_lead_by_chat_id(self):
        st = state(leads=[1, 2, 3], allowed_users=["3", "carol"])
        self.assertTrue(A.remove_lead(st, "3"))
        self.assertEqual(st["leads"], [1, 2])
        self.assertNotIn("3", st["lead_names"])
        self.assertEqual(st["settings"]["allowed_users"], ["carol"])
        self.assertFalse(A.remove_lead(st, "999"))

    def test_invite_code_is_url_safe(self):
        st = state()
        code = A.new_invite(st)
        self.assertRegex(code, r"^[A-Za-z0-9_-]{8,64}$")
        self.assertEqual(st["settings"]["invite_code"], code)


if __name__ == "__main__":
    unittest.main()
