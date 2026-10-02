import pytest

from clutchbot.osu.links import MatchLinkError, parse_match_id


@pytest.mark.parametrize(
    "text",
    [
        "117358530",
        "  117358530  ",
        "mp 117358530",
        "MP  117358530",
        "https://osu.ppy.sh/community/matches/117358530",
        "http://osu.ppy.sh/community/matches/117358530",
        "osu.ppy.sh/community/matches/117358530",
        "https://www.osu.ppy.sh/community/matches/117358530",
        "https://osu.ppy.sh/community/matches/117358530/",
        "https://osu.ppy.sh/community/matches/117358530?foo=1",
        "https://osu.ppy.sh/community/matches/117358530#events",
        "https://osu.ppy.sh/matches/117358530",
        "https://osu.ppy.sh/mp/117358530",
        "osu.ppy.sh/mp/117358530",
        "HTTPS://OSU.PPY.SH/MP/117358530",
    ],
)
def test_valid(text: str) -> None:
    assert parse_match_id(text) == 117358530


@pytest.mark.parametrize(
    "text",
    [
        "",
        "abc",
        "0",
        "-5",
        "1.5",
        "117358530abc",
        "mp",
        "https://osu.ppy.sh/users/117358530",
        "https://osu.ppy.sh/community/matches/",
        "https://example.com/mp/117358530",
        "https://osu.ppy.sh.evil.com/mp/117358530",
    ],
)
def test_invalid(text: str) -> None:
    with pytest.raises(MatchLinkError):
        parse_match_id(text)
