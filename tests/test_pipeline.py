import pytest

from clutchbot.osu.models import Game
from clutchbot.processing.pipeline import ProcessingError, process_match
from clutchbot.processing.tournament import TournamentName
from clutchbot.processing.trim import SkipReason
from clutchbot.summary import format_summary, map_mods
from tests.conftest import (
    HEAD_TO_HEAD_ID,
    WARMUP_MATCH_ID,
    load_detail,
    replace_games,
)

BLB = TournamentName("BLB", "BLB Tournament", "Hessen", "Berlin")
DBHS = TournamentName("DBHS", "DBHS Tournament", "NINERIK", "Welter")


def test_process_whole_match() -> None:
    processed = process_match(load_detail(WARMUP_MATCH_ID), BLB)

    assert len(processed.trimmed.skipped) == 2
    assert (processed.result.red_wins, processed.result.blue_wins) == (5, 1)
    assert len(processed.costs) == 11
    assert [c.map_number for c in processed.clutches] == [1, 4]


def test_settings_are_passed_on() -> None:
    processed = process_match(
        load_detail(WARMUP_MATCH_ID), BLB, warmup_games_checked=0, clutch_threshold_percent=10
    )

    assert processed.trimmed.skipped == []
    assert len(processed.result.games) == 8


def test_unfinished_match_is_refused() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    unfinished = detail.match.model_copy(update={"end_time": None})
    running = detail.model_copy(update={"match": unfinished})

    with pytest.raises(ProcessingError, match="isn't finished"):
        process_match(running, DBHS)


def test_result_errors_become_processing_errors() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    no_games = detail.model_copy(update={"events": [e for e in detail.events if e.game is None]})

    with pytest.raises(ProcessingError, match="No games"):
        process_match(no_games, DBHS)


def test_summary() -> None:
    summary = format_summary(process_match(load_detail(HEAD_TO_HEAD_ID), DBHS))
    lines = summary.splitlines()

    assert lines[0] == "DBHS Tournament | DBHS: (NINERIK) vs (Welter)"
    assert lines[1] == "https://osu.ppy.sh/mp/117358530"
    assert lines[3] == "NINERIK 3 - 7 Welter  (Winner: Welter)"
    assert "  red   NINERIK               2.52  (10 maps)" in lines
    assert "  blue  Welter                2.16  (10 maps)" in lines
    assert lines[-1] == (
        "  10. Gram VS Camellia - Ragnarok [Desolation]: Welter by 0.63% (342,711 vs 340,567)"
    )


def test_summary_lists_skipped_warmups() -> None:
    summary = format_summary(process_match(load_detail(WARMUP_MATCH_ID), BLB))

    assert summary.count("(warmup)") == 2
    assert "Hessen 5 - 1 Berlin  (Winner: Hessen)" in summary


def test_map_mods_leave_out_nf() -> None:
    game = load_detail(HEAD_TO_HEAD_ID).games[0]  # NF + DT

    assert map_mods(game) == " +DT"
    assert map_mods(game.model_copy(update={"mods": ["NF"]})) == ""


def without_first_score(game: Game) -> Game:
    """The same map, but one player's score is missing (a disconnect, or fewer players)"""
    return game.model_copy(update={"scores": game.scores[1:]})


def test_an_uneven_last_map_after_the_match_was_decided_is_dropped() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)  # 3-6 before the last map
    changed = replace_games(detail, {9: without_first_score(detail.games[9])})

    processed = process_match(changed, DBHS)

    assert [s.reason for s in processed.trimmed.skipped] == [SkipReason.FUN_TIEBREAKER]
    assert (processed.result.red_wins, processed.result.blue_wins) == (3, 6)


def test_an_uneven_real_tiebreaker_counts() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    # the match ends after map 5, played at 2-2 with a player missing
    aborted = {i: detail.games[i].model_copy(update={"scores": []}) for i in range(5, 10)}
    changed = replace_games(detail, {4: without_first_score(detail.games[4]), **aborted})

    processed = process_match(changed, DBHS)

    assert len(processed.result.games) == 5
    assert SkipReason.FUN_TIEBREAKER not in [s.reason for s in processed.trimmed.skipped]
