"""The live login page's "Check sign-in" button: the provider's own check runs on the page as it is, then on the
provider's home address, and a pass completes the login. No browser: the page, context and provider are fakes."""

from types import SimpleNamespace

from chatsherpa.login.server import LoginServer
from chatsherpa.store import MemorySessionStore

STATE = {"cookies": [{"name": "sid", "value": "v", "domain": ".example.test", "path": "/"}], "origins": []}


class FakeProvider:
    name = "fakeprov"
    home_url = "https://app.example.test/home"
    # Each probe answers from this list, so a test can say "not yet, then yes".
    answers: list = []
    probes = 0

    @classmethod
    def attach(cls, page, config=None):
        prov = SimpleNamespace()

        async def is_logged_in():
            cls.probes += 1
            return cls.answers.pop(0) if cls.answers else False

        prov.is_logged_in = is_logged_in
        return prov


class FakePage:
    def __init__(self, url):
        self.url = url
        self.visits = []

    async def goto(self, url, **_):
        self.visits.append(url)
        self.url = url


class FakeContext:
    async def storage_state(self):
        return STATE


def _setup(answers, url):
    FakeProvider.answers = list(answers)
    FakeProvider.probes = 0
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com")
    pending = server.broker.create(FakeProvider, "user-1")
    pending.page = FakePage(url)
    pending.context = FakeContext()
    sent = []

    async def send_json(obj):
        sent.append(obj)

    return server, pending, sent, send_json


async def test_a_pass_on_the_current_page_completes_the_login():
    server, pending, sent, send_json = _setup([True], "https://accounts.example.test/signin")
    await server._manual_check(pending, send_json)
    assert pending.page.visits == []  # the page is checked as it is first
    assert sent == []
    assert await pending.handle.future == STATE
    assert await server.store.get("user-1", "fakeprov") is not None


async def test_a_fail_off_the_provider_site_checks_again_on_its_home_address():
    server, pending, sent, send_json = _setup([False, True], "https://accounts.example.test/signin")
    await server._manual_check(pending, send_json)
    assert pending.page.visits == ["https://app.example.test/home"]
    assert FakeProvider.probes == 2
    assert await pending.handle.future == STATE


async def test_a_fail_on_the_provider_site_is_reported_and_the_login_stays_open():
    server, pending, sent, send_json = _setup([False], "https://app.example.test/home")
    await server._manual_check(pending, send_json)
    assert pending.page.visits == []
    assert sent == [{"type": "check", "ok": False,
                     "message": "Not signed in yet. Finish signing in, then press Check again."}]
    assert not pending.handle.future.done()
    assert pending.checking is False


async def test_a_second_press_while_a_check_runs_does_nothing():
    server, pending, sent, send_json = _setup([True], "https://app.example.test/home")
    pending.checking = True
    await server._manual_check(pending, send_json)
    assert FakeProvider.probes == 0
    assert sent == []
