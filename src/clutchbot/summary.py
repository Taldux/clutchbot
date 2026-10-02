from clutchbot.osu.models import Game
from clutchbot.processing.pipeline import ProcessedMatch

MATCH_URL = "https://osu.ppy.sh/mp/{match_id}"


def format_summary(processed: ProcessedMatch) -> str:
    detail = processed.detail
    result = processed.result
    users = detail.users_by_id

    winner = result.winner
    outcome = "Draw" if winner is None else f"Winner: {result.team(winner).name}"
    lines = [
        f"{processed.names.tournament} | {detail.match.name}",
        MATCH_URL.format(match_id=detail.match.id),
        "",
        f"{result.red.name} {result.red_wins} - {result.blue_wins} {result.blue.name}  ({outcome})",
    ]
    lines += [f"Skipped: {map_title(s.game)} ({s.reason})" for s in processed.trimmed.skipped]

    lines += ["", "Maps:"]
    for number, game_result in enumerate(result.games, start=1):
        map_winner = game_result.winner
        won_by = "tie" if map_winner is None else result.team(map_winner).name
        lines.append(
            f"  {number:>2}. {map_title(game_result.game)}{map_mods(game_result.game)}  "
            f"{game_result.red_score:,} - {game_result.blue_score:,}  {won_by}"
        )

    lines += ["", "Match costs:"]
    for cost in processed.costs:
        user = users.get(cost.user_id)
        name = user.username if user is not None else str(cost.user_id)
        lines.append(f"  {cost.side:<4}  {name:<20}  {cost.cost:.2f}  ({cost.maps_played} maps)")

    lines += ["", "Clutch maps:"]
    if not processed.clutches:
        lines.append("  none")
    for clutch in processed.clutches:
        game_result = clutch.game
        scores = sorted((game_result.red_score, game_result.blue_score), reverse=True)
        lines.append(
            f"  {clutch.map_number:>2}. {map_title(game_result.game)}: "
            f"{result.team(clutch.winner).name} by {clutch.margin_percent:.2f}% "
            f"({scores[0]:,} vs {scores[1]:,})"
        )
    return "\n".join(lines)


def map_title(game: Game) -> str:
    if game.beatmap is None:
        return f"Beatmap {game.beatmap_id}"
    beatmapset = game.beatmap.beatmapset
    return f"{beatmapset.artist} - {beatmapset.title} [{game.beatmap.version}]"


def map_mods(game: Game) -> str:
    """For example " +HR", without NF"""
    mods = [mod for mod in game.mods if mod != "NF"]
    return f" +{''.join(mods)}" if mods else ""
