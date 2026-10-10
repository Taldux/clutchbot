import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer
from pydantic import SecretStr

from clutchbot.acronyms import (
    AcronymsError,
    AcronymsFile,
    check_entry,
    load_acronyms,
    save_acronyms,
)
from clutchbot.alerts import Alerter, Priority, split_topic_url
from clutchbot.config import ConfigError, Settings, load_settings
from clutchbot.listener.loop import heartbeat_age, max_heartbeat_age, run_listener
from clutchbot.listener.poll import Listener
from clutchbot.listener.publishing import AlreadyPostedError, posted_so_far, publish_match
from clutchbot.logging_setup import setup_logging
from clutchbot.metrics import start_metrics_server
from clutchbot.osu.client import OsuClient
from clutchbot.osu.links import MatchLinkError, parse_match_id
from clutchbot.osu.models import MatchDetail
from clutchbot.processing.pipeline import ProcessedMatch, ProcessingError, process_match
from clutchbot.processing.tournament import describe_lobby
from clutchbot.render.browser import CardRenderer
from clutchbot.render.cards import MatchCards, render_match_cards
from clutchbot.render.font_check import check_fonts, sample_sheet
from clutchbot.render.images import ImageCache
from clutchbot.storage.database import Database
from clutchbot.summary import format_summary
from clutchbot.twitter.client import XAccount, XApiError, XClient
from clutchbot.twitter.poster import PublishError, post_url
from clutchbot.twitter.posts import PlannedPost, format_plan, plan_posts

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger(__name__)


def _fail(message: str, code: int = 1) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _startup(dry_run: bool | None = None) -> Settings:
    try:
        settings = load_settings(dry_run)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None
    setup_logging(settings)
    log.debug("Settings loaded, logging at %s", settings.log_level)
    return settings


@app.callback()
def main() -> None:
    """ClutchBot: posts osu! tournament match results to Twitter"""


@app.command("check-config")
def check_config() -> None:
    """Validates configuration"""
    settings = _startup()
    typer.echo("Configuration OK")
    typer.echo(f"  dry_run:                  {settings.dry_run}")
    typer.echo(f"  poll_interval_seconds:    {settings.poll_interval_seconds}")
    typer.echo(f"  match_expiry_hours:       {settings.match_expiry_hours}")
    typer.echo(f"  clutch_threshold_percent: {settings.clutch_threshold_percent}")
    typer.echo(f"  warmup_games_checked:     {settings.warmup_games_checked}")
    typer.echo(f"  ez_multiplier:            {settings.ez_multiplier}")
    typer.echo(f"  data_dir:                 {settings.data_dir}")
    typer.echo(f"  acronyms_path:            {settings.resolved_acronyms_path}")
    typer.echo(f"  log_level:                {settings.log_level}")
    typer.echo(f"  log_format:               {settings.log_format}")
    typer.echo(f"  metrics_port:             {settings.metrics_port or 'off'}")
    typer.echo(f"  ntfy_url:                 {'set' if settings.ntfy_url else 'not set'}")


@app.command("check-x")
def check_x() -> None:
    settings = _startup()
    account = asyncio.run(_ask_x_who_we_are(_x_keys(settings)))
    typer.echo(f"X keys OK: posts will come from @{account.username} ({account.name})")


XKeys = tuple[SecretStr, SecretStr, SecretStr, SecretStr]


def _x_keys(settings: Settings) -> XKeys:
    consumer_key = settings.twitter_consumer_key
    consumer_secret = settings.twitter_consumer_secret
    access_token = settings.twitter_access_token
    access_secret = settings.twitter_access_secret
    if (
        consumer_key is None
        or consumer_secret is None
        or access_token is None
        or access_secret is None
    ):
        _fail("Set all four TWITTER_* keys in .env first", code=2)
    return consumer_key, consumer_secret, access_token, access_secret


async def _ask_x_who_we_are(keys: XKeys) -> XAccount:
    try:
        async with XClient(*keys) as x:
            return await x.get_me()
    except XApiError as exc:
        _fail(f"X rejected the keys: {exc}")
    except httpx.TransportError as exc:
        _fail(f"Couldn't reach the X API: {type(exc).__name__}")


@app.command()
def process(
    # "dry run" means its not posted to twitter, just saved
    match: Annotated[str, typer.Argument(help="Match id or link, e.g. osu.ppy.sh/mp/117358530")],
    dry_run: Annotated[
        bool | None,
        typer.Option("--dry-run/--no-dry-run", help="Overrides DRY_RUN from the config"),
    ] = None,
    output: Annotated[Path, typer.Option(help="Folder for dry-run images")] = Path("output"),
    yes: Annotated[bool, typer.Option("--yes", help="Post without asking first")] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Post again even if this match was already posted")
    ] = False,
) -> None:
    settings = _startup(dry_run)
    x_keys = None if settings.dry_run else _x_keys(settings)
    processed = _fetch_and_process(settings, match)
    typer.echo(format_summary(processed))

    if x_keys is None:
        cards = asyncio.run(_render_cards(settings, processed))
        folder = _save_cards(output, processed, cards)
        typer.echo(f"\nDry run, nothing posted. Images saved in {folder}\n")
        typer.echo(format_plan(plan_posts(processed, cards)))
        return

    with Database(settings.db_path) as db:
        # read-only until user confirms
        try:
            already_posted = posted_so_far(db, processed.detail.match.id, force=force)
        except AlreadyPostedError as exc:
            _echo_posts(exc.match.post_ids)
            _fail(f"{exc}, use --force to post it again as a new thread")

        cards = asyncio.run(_render_cards(settings, processed))
        plan = plan_posts(processed, cards)
        typer.echo(f"\n{format_plan(plan)}\n")
        if already_posted:
            _echo_posts(already_posted)
            typer.echo(f"Resuming: {len(already_posted)} of these are already on X\n")
        remaining = len(plan) - len(already_posted)
        if not yes and not typer.confirm(f"Publish {remaining} post(s) on X?"):
            _fail("Nothing posted")
        _echo_posts(asyncio.run(_publish(x_keys, db, processed, plan, force)))


async def _publish(
    keys: XKeys, db: Database, processed: ProcessedMatch, plan: list[PlannedPost], force: bool
) -> list[str]:
    try:
        async with XClient(*keys) as x:
            return await publish_match(db, x, processed, plan, force=force)
    except PublishError as exc:
        _echo_posts(exc.posted_ids)
        _fail(str(exc))


def _echo_posts(post_ids: list[str]) -> None:
    for number, post_id in enumerate(post_ids, start=1):
        typer.echo(f"Post {number}: {post_url(post_id)}")


@app.command()
def listen(
    dry_run: Annotated[
        bool | None,
        typer.Option("--dry-run/--no-dry-run", help="Overrides DRY_RUN from the config"),
    ] = None,
) -> None:
    # listener for matches
    settings = _startup(dry_run)
    acronyms = _load_acronyms_required(settings)
    x_keys = None if settings.dry_run else _x_keys(settings)
    asyncio.run(_listen(settings, acronyms, x_keys, _ntfy_url(settings)))


@app.command("check-alerts")
def check_alerts() -> None:
    """Test notif to ntfy"""
    settings = _startup()
    ntfy_url = _ntfy_url(settings)
    if ntfy_url is None:
        _fail("Set NTFY_URL in .env first, e.g. https://ntfy.sh/<your private topic>", code=2)

    async def send_test() -> None:
        async with Alerter(ntfy_url) as alerter:
            await alerter.send(
                "ClutchBot test", "Alerts work", priority=Priority.LOW, tags=["white_check_mark"]
            )

    asyncio.run(send_test())
    typer.echo("Test notification sent (check your phone; failures are logged above)")


@app.command("check-fonts")
def check_fonts_command(
    output: Annotated[Path, typer.Option(help="Where to save the sample image")] = Path(
        "output/fonts.png"
    ),
) -> None:
    _startup()

    async def run() -> tuple[dict[str, bool], bytes]:
        async with CardRenderer() as renderer:
            return await check_fonts(renderer), await renderer.render(sample_sheet())

    results, sheet = asyncio.run(run())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(sheet)
    for name, ok in results.items():
        typer.echo(f"  {name:<15} {'OK' if ok else 'MISSING: drawn as empty boxes'}")
    typer.echo(f"Sample image saved to {output}")
    if not all(results.values()):
        _fail("Some scripts have no font; install the fallback fonts (see the Dockerfile)")


def _ntfy_url(settings: Settings) -> str | None:
    if settings.ntfy_url is None:
        return None
    url = settings.ntfy_url.get_secret_value()
    try:
        split_topic_url(url)
    except ValueError as exc:
        _fail(str(exc), code=2)
    return url


async def _listen(
    settings: Settings, acronyms: AcronymsFile, x_keys: XKeys | None, ntfy_url: str | None
) -> None:
    async with Alerter(ntfy_url) as alerter:
        try:
            await _run_service(settings, acronyms, x_keys, alerter)
        except Exception as exc:
            # Ctrl+C and docker stop don't end up here, so this is an actual crash
            await alerter.send(
                "ClutchBot stopped",
                f"The listener crashed: {type(exc).__name__}: {exc}",
                priority=Priority.URGENT,
                tags=["rotating_light"],
            )
            raise


async def _run_service(
    settings: Settings, acronyms: AcronymsFile, x_keys: XKeys | None, alerter: Alerter
) -> None:
    stop = asyncio.Event()
    _stop_on_signals(stop)
    async with contextlib.AsyncExitStack() as stack:
        osu = await stack.enter_async_context(
            OsuClient(settings.osu_client_id, settings.osu_client_secret)
        )
        images = await stack.enter_async_context(ImageCache(settings.image_cache_dir))
        db = stack.enter_context(Database(settings.db_path))
        x: XClient | None = None
        if x_keys is not None:
            x = await stack.enter_async_context(XClient(*x_keys))
            account = await x.get_me()  # fails early on bad keys
            mode = f"posting as @{account.username}"
        else:
            mode = f"dry run, cards go to {settings.dry_run_dir}"
        if settings.metrics_port is not None:
            start_metrics_server(settings.metrics_port, dry_run=settings.dry_run)

        async def render_cards(processed: ProcessedMatch) -> MatchCards:
            # new browser per match in case it hangs
            async with CardRenderer(images.get) as renderer:
                return await render_match_cards(renderer, processed)

        listener = Listener(
            osu=osu,
            db=db,
            acronyms=acronyms,
            render_cards=render_cards,
            x=x,
            dry_run_dir=settings.dry_run_dir,
            match_expiry=timedelta(hours=settings.match_expiry_hours),
            warmup_games_checked=settings.warmup_games_checked,
            clutch_threshold_percent=settings.clutch_threshold_percent,
            ez_multiplier=settings.ez_multiplier,
            alerter=alerter,
        )
        log.info(
            "Listening for %d tournament(s) every %d s, %s. Stop with Ctrl+C",
            len(acronyms.current),
            settings.poll_interval_seconds,
            mode,
        )
        await alerter.send(
            "ClutchBot started",
            f"Listening for {len(acronyms.current)} tournament(s), {mode}",
            priority=Priority.LOW,
            tags=["robot"],
        )
        await run_listener(
            listener,
            stop,
            interval_seconds=settings.poll_interval_seconds,
            heartbeat_path=settings.heartbeat_path,
            image_cache_dir=settings.image_cache_dir,
        )


def _stop_on_signals(stop: asyncio.Event) -> None:
    # first Ctrl+C / SIGTERM lets current poll finish, second one no
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)

    def request_stop(*_: object) -> None:
        log.info("Stopping after the current poll (press Ctrl+C again to quit at once)")
        loop.call_soon_threadsafe(stop.set)
        for sig in signals:
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    for sig in signals:
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            signal.signal(sig, request_stop)


acronyms_app = typer.Typer(
    no_args_is_help=True, help="Show or change which tournaments are tracked"
)
app.add_typer(acronyms_app, name="acronyms")

_PICKED_UP = "A running listener picks this up at its next poll."


@acronyms_app.command("list")
def acronyms_list() -> None:
    settings = _startup()
    path = settings.resolved_acronyms_path
    acronyms = _read_acronyms(path, missing_ok=False)
    width = max((len(acronym) for acronym in acronyms), default=0)
    for acronym, tournament in sorted(acronyms.items()):
        typer.echo(f"{acronym:<{width}}  {tournament}")
    typer.echo(f"{len(acronyms)} tournament(s) in {path}")


@acronyms_app.command("add")
def acronyms_add(
    acronym: Annotated[str, typer.Argument(help="What lobby names start with, e.g. RT")],
    tournament: Annotated[str, typer.Argument(help='Name on the cards, e.g. "Random Tournament"')],
    replace: Annotated[
        bool, typer.Option("--replace", help="Change the name of an acronym already tracked")
    ] = False,
) -> None:
    settings = _startup()
    path = settings.resolved_acronyms_path
    try:
        acronym, tournament = check_entry(acronym, tournament)
    except AcronymsError as exc:
        _fail(str(exc), code=2)
    acronyms = _read_acronyms(path, missing_ok=True)
    old = acronyms.get(acronym)
    if old == tournament:
        typer.echo(f"{acronym} is already tracked as {tournament!r}, nothing changed")
        return
    if old is not None and not replace:
        _fail(f"{acronym} is already tracked as {old!r}, add --replace to change it")
    save_acronyms(path, {**acronyms, acronym: tournament})
    typer.echo(f"{'Renamed' if old else 'Added'} {acronym}: {tournament}")
    typer.echo(_PICKED_UP)
    if old is None:
        typer.echo(
            "Lobbies opened before that aren't picked up: post those with "
            "`clutchbot process <match id>` once they finish."
        )


@acronyms_app.command("remove")
def acronyms_remove(
    acronym: Annotated[str, typer.Argument(help="The acronym to stop tracking, e.g. RT")],
) -> None:
    settings = _startup()
    path = settings.resolved_acronyms_path
    acronyms = _read_acronyms(path, missing_ok=False)
    acronym = acronym.strip().upper()
    if acronym not in acronyms:
        _fail(f"{acronym} isn't in {path}")
    if len(acronyms) == 1:
        _fail(f"{acronym} is the only tournament left, and the listener needs at least one")
    tournament = acronyms.pop(acronym)
    save_acronyms(path, acronyms)
    typer.echo(f"Removed {acronym} ({tournament})")
    typer.echo(_PICKED_UP)


def _read_acronyms(path: Path, *, missing_ok: bool) -> dict[str, str]:
    if missing_ok and not path.exists():
        return {}
    try:
        return load_acronyms(path)
    except AcronymsError as exc:
        _fail(str(exc), code=2)


@app.command()
def healthcheck() -> None:
    """Exit 1 if the last poll is too long ago (Docker health check)"""
    settings = _startup()
    age = heartbeat_age(settings.heartbeat_path, datetime.now(UTC))
    limit = max_heartbeat_age(settings.poll_interval_seconds)
    if age is None:
        _fail(f"Unhealthy: no readable heartbeat at {settings.heartbeat_path}")
    if age > limit:
        _fail(f"Unhealthy: last poll finished {age.total_seconds():.0f} s ago (limit {limit})")
    typer.echo(f"OK: last poll finished {age.total_seconds():.0f} s ago")


def _load_acronyms_required(settings: Settings) -> AcronymsFile:
    try:
        return AcronymsFile(settings.resolved_acronyms_path)
    except AcronymsError as exc:
        _fail(str(exc), code=2)


@app.command()
def render(
    match: Annotated[str, typer.Argument(help="Match id or link, e.g. osu.ppy.sh/mp/117358530")],
    output: Annotated[Path, typer.Option(help="Folder for the images")] = Path("output"),
) -> None:
    """Draw the cards for a match and save as PNG"""
    settings = _startup()
    processed = _fetch_and_process(settings, match)
    cards = asyncio.run(_render_cards(settings, processed))
    folder = _save_cards(output, processed, cards)
    for card in cards.all:
        typer.echo(f"Saved {folder / card.file_name}")


async def _render_cards(settings: Settings, processed: ProcessedMatch) -> MatchCards:
    async with (
        ImageCache(settings.image_cache_dir) as images,
        CardRenderer(images.get) as renderer,
    ):
        return await render_match_cards(renderer, processed)


def _save_cards(output: Path, processed: ProcessedMatch, cards: MatchCards) -> Path:
    folder = output / str(processed.detail.match.id)
    folder.mkdir(parents=True, exist_ok=True)
    for card in cards.all:
        (folder / card.file_name).write_bytes(card.png)
    return folder


def _fetch_and_process(settings: Settings, match: str) -> ProcessedMatch:
    try:
        match_id = parse_match_id(match)
    except MatchLinkError as exc:
        _fail(str(exc), code=2)

    acronyms = _load_acronyms_if_present(settings)
    detail = asyncio.run(_fetch_match(settings, match_id))
    names = describe_lobby(detail.match.name, acronyms)
    try:
        processed = process_match(
            detail,
            names,
            warmup_games_checked=settings.warmup_games_checked,
            clutch_threshold_percent=settings.clutch_threshold_percent,
            ez_multiplier=settings.ez_multiplier,
        )
    except ProcessingError as exc:
        _fail(str(exc))

    for skipped in processed.trimmed.skipped:
        log.info("Match %d: skipped game %d (%s)", match_id, skipped.game.id, skipped.reason)
    return processed


def _load_acronyms_if_present(settings: Settings) -> dict[str, str]:
    path = settings.resolved_acronyms_path
    if not path.exists():
        log.warning("No acronyms file at %s, tournament names will be acronyms", path)
        return {}
    try:
        return load_acronyms(path)
    except AcronymsError as exc:
        _fail(str(exc), code=2)


async def _fetch_match(settings: Settings, match_id: int) -> MatchDetail:
    try:
        async with OsuClient(settings.osu_client_id, settings.osu_client_secret) as osu:
            return await osu.get_match(match_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == httpx.codes.NOT_FOUND:
            _fail(f"Match {match_id} not found (or private)")
        _fail(f"osu! API error for match {match_id}: HTTP {exc.response.status_code}")
    except httpx.TransportError as exc:
        _fail(f"Couldn't reach the osu! API: {type(exc).__name__}")
