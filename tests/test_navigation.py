from html.parser import HTMLParser
from pathlib import Path


class NavigationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_navigation = False
        self.current_link: dict[str, str] | None = None
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "nav" and "primary-nav" in values.get("class", "").split():
            self.in_navigation = True
        elif self.in_navigation and tag == "a":
            self.current_link = values
            self.current_link["text"] = ""

    def handle_data(self, data: str) -> None:
        if self.current_link is not None:
            self.current_link["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_link is not None:
            self.current_link["text"] = self.current_link["text"].strip()
            self.links.append(self.current_link)
            self.current_link = None
        elif tag == "nav" and self.in_navigation:
            self.in_navigation = False


def navigation(path: str) -> list[dict[str, str]]:
    parser = NavigationParser()
    parser.feed(Path(path).read_text(encoding="utf-8"))
    return parser.links


def test_primary_navigation_is_identical_and_marks_current_page():
    pages = {
        "index.html": "index.html",
        "deck.html": "deck.html",
        "arena.html": "arena.html",
        "review.html": "review.html",
    }
    expected = [
        ("index.html", "Cards"),
        ("deck.html", "Deck Lab"),
        ("arena.html", "Arena"),
        ("review.html", "Rule Review"),
    ]
    for page, current in pages.items():
        links = navigation(page)
        assert [(link["href"], link["text"]) for link in links] == expected
        assert [link["href"] for link in links if link.get("aria-current") == "page"] == [current]


def test_deck_components_load_before_the_page_controller():
    markup = Path("deck.html").read_text(encoding="utf-8")
    assert markup.index("components.js") < markup.index("deck.js")


def test_arena_has_game_field_and_fullscreen_control():
    markup = Path("arena.html").read_text(encoding="utf-8")
    assert 'id="gameTable"' in markup
    assert 'id="fullscreenButton"' in markup
    assert 'id="setupOverlay"' in markup
    assert 'id="setupActionList"' in markup
    assert 'id="opponentHand"' in markup
    assert 'class="field-side opponent-side"' in markup
    assert 'class="field-side player-side"' in markup
    assert 'class="game-ball"' in markup
    assert markup.index("components.js") < markup.index("arena.js")
