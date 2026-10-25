"""Trusted players by name or by id, and auto replies that call the player by name."""

from unclefarmer import messages, trust


class Link:
    def send(self, line):
        return {"idof Sara": "3005", "idof Kim": "2001"}.get(line, "0")


def test_names_and_ids_mixed_in_one_list():
    t = trust.Trusted(["Kim", " 3005 ", "", "ALI"])
    assert t.names == {"kim", "ali"} and t.ids == {3005}
    assert t.has("kim") and t.has("Ali")
    assert t.has("Sara", pid=3005)                 # a message from id 3005, whatever the name
    assert not t.has("Bob", pid=4000)
    assert t.has("Sara", link=Link())              # only a name (a team invite): the game knows her id
    assert not t.has("Bob", link=Link())
    assert not trust.Trusted([])


def test_replies_call_the_player_by_name():
    cfg = {"pm_call_by_name": True}
    assert messages.address("Hi {name}! I'm away.", "Kim", cfg) == "Hi Kim! I'm away."
    assert messages.address("I'm busy right now.", "Kim", cfg) == "Kim, I'm busy right now."
    assert messages.address("I'm busy right now.", "Kim", {"pm_call_by_name": False}) == "I'm busy right now."
    assert messages.address("{name}, back soon, {name}", "Kim", {"pm_call_by_name": False}) == "Kim, back soon, Kim"
