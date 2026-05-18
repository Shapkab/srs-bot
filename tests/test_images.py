"""Image-card storage + re-upload.

Two contracts pinned here:

* /addimage path → row with file_id + sha256 populated; bytes on disk
  at ``<IMAGE_DIR>/<sha>.jpg``; deduped on identical content.
* Re-upload path → given a card with a known sha256 and a cleared
  file_id, the script writes a fresh file_id back to the DB without
  touching Telegram (the bot is stubbed).
"""

from __future__ import annotations

import asyncio
import io
from datetime import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from scripts.reupload_images import reupload_images
from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card
from src.handlers.add_card import _download_and_hash, _finalize_image
from src.utils.image_store import sha256_hex, store_bytes

# A tiny "image" body — content doesn't matter, only that hashing /
# round-tripping behaves.
_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"hello-srs-bot-test-image" * 16


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        bot_token="x",
        db_path=tmp_path / "unused-by-test.db",
        owner_telegram_id=1,
        timezone="UTC",
        reminder_time=time(9, 0),
        log_level="INFO",
        image_dir=tmp_path / "images",
    )


# ---------------------------------------------------------------------------
# Pure helpers


def test_store_bytes_returns_stable_sha_and_writes_content(tmp_path: Path) -> None:
    """Idempotency contract: the sha is the SHA-256 of the bytes, the
    target file exists at <IMAGE_DIR>/<sha>.jpg and contains exactly
    the bytes passed in. Calling twice with the same bytes returns the
    same sha and the target stays correct (no claim about mtime — see
    Phase 9.3, which dropped the if-not-exists guard in favour of an
    atomic os.replace)."""
    image_dir = tmp_path / "images"

    sha_a = store_bytes(image_dir, _FAKE_JPEG)
    assert sha_a == sha256_hex(_FAKE_JPEG)
    target = image_dir / f"{sha_a}.jpg"
    assert target.exists()
    assert target.read_bytes() == _FAKE_JPEG

    sha_b = store_bytes(image_dir, _FAKE_JPEG)
    assert sha_b == sha_a
    assert target.read_bytes() == _FAKE_JPEG


def test_store_bytes_is_atomic(tmp_path: Path) -> None:
    """Phase 9.3 contract: ``store_bytes`` writes via a sibling ``.tmp``
    then ``os.replace`` — so concurrent writers landing identical bytes
    never observe a half-written file, and no ``.tmp`` lingers after.
    """
    import threading

    image_dir = tmp_path / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    sha = sha256_hex(_FAKE_JPEG)
    target = image_dir / f"{sha}.jpg"

    # Two threads racing on the same content.
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []

    def _writer() -> None:
        try:
            barrier.wait(timeout=2)
            results.append(store_bytes(image_dir, _FAKE_JPEG))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=_writer)
    t2 = threading.Thread(target=_writer)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert errors == [], errors
    assert results == [sha, sha]
    assert target.read_bytes() == _FAKE_JPEG
    # The atomic-rename source must be cleaned up.
    assert not any(p.name.endswith(".tmp") for p in image_dir.iterdir())


# ---------------------------------------------------------------------------
# /addimage finalize → DB row + on-disk file


class _StubFromUser:
    id = 1
    username = "t"


class _StubMessage:
    """Stand-in for aiogram Message during the /addimage finalize step."""

    def __init__(self) -> None:
        self.from_user = _StubFromUser()
        self.answer = AsyncMock()


class _StubFSMContext:
    """Stand-in for an aiogram FSMContext — get_data / clear only."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self._cleared = False

    async def get_data(self) -> dict[str, object]:
        return dict(self._payload)

    async def clear(self) -> None:
        self._cleared = True


def _make_stub_bot(payloads: dict[str, bytes]) -> AsyncMock:
    """Construct an AsyncMock Bot whose download(file_id, destination)
    writes the configured bytes into the destination buffer."""

    async def _download(file_id: str, destination: io.BytesIO) -> None:
        destination.write(payloads[file_id])

    bot = AsyncMock()
    bot.download.side_effect = _download
    return bot


def test_addimage_finalize_writes_db_row_and_image_file(tmp_path: Path) -> None:
    """Drive the finalize step directly with stubbed bot/FSM — covers
    the contract that file_id + sha256 land on the row and the bytes
    land at the documented path."""
    settings = _settings(tmp_path)
    bot = _make_stub_bot(
        {"FRONT_FILE_ID": _FAKE_JPEG, "BACK_FILE_ID": b"different-bytes-here"}
    )
    fsm = _StubFSMContext(
        {"front_file_id": "FRONT_FILE_ID", "front_text": "alpha"}
    )
    msg = _StubMessage()

    asyncio.run(
        _finalize_image(
            msg,  # type: ignore[arg-type]
            fsm,  # type: ignore[arg-type]
            bot,  # type: ignore[arg-type]
            settings,
            back_file_id="BACK_FILE_ID",
            back_text="beta",
        )
    )

    # FSM was cleared and the user got a confirmation.
    assert fsm._cleared
    msg.answer.assert_awaited_once()
    assert "Added card" in msg.answer.call_args.args[0]

    # The Card row has both halves linked.
    with session_scope() as s:
        card = s.scalar(select(Card))
        assert card is not None
        assert card.front == "alpha"
        assert card.back == "beta"
        assert card.front_image_file_id == "FRONT_FILE_ID"
        assert card.back_image_file_id == "BACK_FILE_ID"
        assert card.front_image_sha256 == sha256_hex(_FAKE_JPEG)
        assert card.back_image_sha256 == sha256_hex(b"different-bytes-here")

    # The bytes are at <IMAGE_DIR>/<sha>.jpg.
    front_path = settings.image_dir / f"{sha256_hex(_FAKE_JPEG)}.jpg"
    back_path = settings.image_dir / f"{sha256_hex(b'different-bytes-here')}.jpg"
    assert front_path.read_bytes() == _FAKE_JPEG
    assert back_path.read_bytes() == b"different-bytes-here"


def test_download_and_hash_returns_known_hash(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    bot = _make_stub_bot({"X": _FAKE_JPEG})
    sha = asyncio.run(_download_and_hash(bot, "X", settings))  # type: ignore[arg-type]
    assert sha == sha256_hex(_FAKE_JPEG)


# ---------------------------------------------------------------------------
# Re-upload script with stubbed bot


def _seed_card_with_image_bytes_on_disk(settings: Settings) -> tuple[int, str]:
    """Insert one image card whose front image bytes live on disk but
    whose ``front_image_file_id`` is empty (simulating a fresh token).
    Returns (card_id, sha)."""
    sha = store_bytes(settings.image_dir, _FAKE_JPEG)
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        card = add_card(
            s, user,
            front="alpha", back="beta",
            front_image_file_id=None,
            front_image_sha256=sha,
        )
        return (card.id, sha)


def _stub_sendphoto_returning_file_id(new_file_id: str) -> AsyncMock:
    """Return a Bot stub whose send_photo returns a Message-like object
    with photo=[..., PhotoSize(file_id=new_file_id)]."""

    class _PhotoSize:
        def __init__(self, file_id: str) -> None:
            self.file_id = file_id

    class _Msg:
        def __init__(self) -> None:
            # Two sizes to verify we pick the largest (the last one).
            self.photo = [_PhotoSize("small_id"), _PhotoSize(new_file_id)]

    bot = AsyncMock()
    bot.send_photo.return_value = _Msg()
    return bot


def test_reupload_writes_fresh_file_id_back_to_db(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    card_id, sha = _seed_card_with_image_bytes_on_disk(settings)

    bot = _stub_sendphoto_returning_file_id("NEW_FRONT_FILE_ID")
    success, total = asyncio.run(reupload_images(settings, bot, dry_run=False))

    assert total == 1
    assert success == 1
    bot.send_photo.assert_awaited_once()
    # The bytes sent match what's on disk.
    kwargs = bot.send_photo.call_args.kwargs
    assert kwargs["chat_id"] == settings.owner_telegram_id
    assert kwargs["photo"].data == _FAKE_JPEG  # BufferedInputFile.data

    # DB now has the fresh file_id; sha is unchanged.
    with session_scope() as s:
        card = s.get(Card, card_id)
        assert card.front_image_file_id == "NEW_FRONT_FILE_ID"
        assert card.front_image_sha256 == sha


def test_reupload_dry_run_does_not_call_bot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    card_id, sha = _seed_card_with_image_bytes_on_disk(settings)

    bot = _stub_sendphoto_returning_file_id("SHOULD_NOT_BE_USED")
    success, total = asyncio.run(reupload_images(settings, bot, dry_run=True))

    assert (success, total) == (0, 1)
    bot.send_photo.assert_not_called()
    out = capsys.readouterr().out
    assert "would reupload" in out
    assert f"card={card_id}" in out

    # DB row untouched.
    with session_scope() as s:
        card = s.get(Card, card_id)
        assert card.front_image_file_id is None
        assert card.front_image_sha256 == sha


def test_reupload_skips_when_local_bytes_missing(tmp_path: Path) -> None:
    """If the on-disk file is missing, the run logs + skips; DB stays
    clean and the script returns success=0 for that card."""
    settings = _settings(tmp_path)
    # Hand-craft a card that points at bytes we never wrote.
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(
            s, user,
            front="a", back="b",
            front_image_file_id=None,
            front_image_sha256="0" * 64,
        )

    bot = _stub_sendphoto_returning_file_id("NEW")
    success, total = asyncio.run(reupload_images(settings, bot, dry_run=False))
    assert total == 1
    assert success == 0
    bot.send_photo.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 9 additions


def test_reupload_targets_filters_already_refreshed(tmp_path: Path) -> None:
    """Phase 9.2: _targets() must exclude sides whose file_id is already
    set, so a retry after a partial-run crash only does the remaining work.
    """
    from scripts.reupload_images import _targets

    settings = _settings(tmp_path)
    sha_a = store_bytes(settings.image_dir, b"image-a-bytes")
    sha_b = store_bytes(settings.image_dir, b"image-b-bytes")
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        # Card A: already refreshed (file_id present). Should NOT appear.
        add_card(
            s, user, front="a", back="x",
            front_image_file_id="FRESH_A", front_image_sha256=sha_a,
        )
        # Card B: needs refresh (sha set, file_id absent). SHOULD appear.
        add_card(
            s, user, front="b", back="y",
            front_image_file_id=None, front_image_sha256=sha_b,
        )

    with session_scope() as s:
        targets = _targets(s)

    assert len(targets) == 1
    (_card_id, side, sha) = targets[0]
    assert side == "front"
    assert sha == sha_b


def test_download_rejects_oversize(tmp_path: Path) -> None:
    """Phase 9.3: bytes larger than settings.max_image_bytes raise
    ValueError and nothing is written to IMAGE_DIR."""
    cap = 64
    settings = _settings(tmp_path)
    settings = Settings(**{**settings.__dict__, "max_image_bytes": cap})

    oversize = b"x" * (cap + 1)
    bot = _make_stub_bot({"BIG": oversize})

    with pytest.raises(ValueError, match="image too large"):
        asyncio.run(_download_and_hash(bot, "BIG", settings))  # type: ignore[arg-type]

    # No file was written to disk.
    if settings.image_dir.exists():
        assert list(settings.image_dir.iterdir()) == []


def test_bot_download_failure_surfaces_user_message(tmp_path: Path) -> None:
    """Phase 9.5: a network/Telegram-side failure during download
    becomes a friendly user message and writes no Card row."""
    settings = _settings(tmp_path)

    async def _explode(file_id: str, destination: io.BytesIO) -> None:
        raise ConnectionError("simulated network blip")

    bot = AsyncMock()
    bot.download.side_effect = _explode

    fsm = _StubFSMContext(
        {"front_file_id": "X", "front_text": "alpha"}
    )
    msg = _StubMessage()

    asyncio.run(
        _finalize_image(
            msg,  # type: ignore[arg-type]
            fsm,  # type: ignore[arg-type]
            bot,  # type: ignore[arg-type]
            settings,
            back_file_id=None, back_text="beta",
        )
    )

    # User saw the friendly recovery message.
    text = msg.answer.call_args.args[0]
    assert "couldn't fetch" in text.lower()
    assert "/addimage" in text

    # No Card landed.
    with session_scope() as s:
        assert s.scalar(select(Card)) is None


def test_cancel_mid_addimage_clears_state_and_writes_nothing(tmp_path: Path) -> None:
    """Phase 9.5: /cancel mid-FSM clears the state and never reaches
    the finalize / DB insert path."""
    from src.handlers.add_card import cmd_addimage_cancel

    state = _StubFSMContext({"front_file_id": "X", "front_text": "halfway"})
    msg = _StubMessage()

    asyncio.run(cmd_addimage_cancel(msg, state))  # type: ignore[arg-type]

    assert state._cleared is True
    assert "cancelled" in msg.answer.call_args.args[0].lower()
    with session_scope() as s:
        assert s.scalar(select(Card)) is None


def test_photo_without_caption_is_rejected(tmp_path: Path) -> None:
    """Phase 9.4.1: photo with empty caption gets rejected with an
    instructive message; FSM state is NOT mutated."""
    from src.handlers.add_card import cmd_addimage_front_photo

    class _PhotoSize:
        def __init__(self, file_id: str) -> None:
            self.file_id = file_id

    class _PhotoMsg(_StubMessage):
        def __init__(self) -> None:
            super().__init__()
            self.photo = [_PhotoSize("small"), _PhotoSize("BIG_FILE_ID")]
            self.caption = "   "  # whitespace-only counts as empty after strip

    state = AsyncMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    msg = _PhotoMsg()

    asyncio.run(cmd_addimage_front_photo(msg, state))  # type: ignore[arg-type]

    text = msg.answer.call_args.args[0]
    assert "caption" in text.lower()
    state.update_data.assert_not_called()
    state.set_state.assert_not_called()


def test_long_caption_falls_back_to_separate_back_message(tmp_path: Path) -> None:
    """Phase 9.4.3: when front+back rendered length exceeds the photo
    caption cap, the photo is sent with the front-only caption AND the
    back is sent as a separate text message."""
    from src.handlers.review import cb_show_answer

    # Seed a card whose back has an image and combined text overflows.
    sha = store_bytes(tmp_path / "images", b"back-image-bytes")
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(
            s, user,
            front="short",
            back="x" * 1100,  # well over the 1000-char cap with HTML overhead
            back_image_file_id="BACK_FILE_ID",
            back_image_sha256=sha,
        )
        state_id = s.scalar(
            select(__import__("src.db.models", fromlist=["ReviewState"]).ReviewState)
        ).id

    # Stub callback exposing the methods we exercise.
    class _CBMessage:
        def __init__(self) -> None:
            self.edit_reply_markup = AsyncMock()
            self.answer_photo = AsyncMock()
            self.answer = AsyncMock()
            self.photo = None  # current is a text "Show answer" stub

    class _CB:
        def __init__(self, data: str) -> None:
            self.data = data
            self.message = _CBMessage()
            self.answer = AsyncMock()

    cb = _CB(f"rv:show:{state_id}")
    asyncio.run(cb_show_answer(cb))  # type: ignore[arg-type]

    # Front-only caption goes onto the photo; back text rides alone.
    cb.message.answer_photo.assert_awaited_once()
    photo_kwargs = cb.message.answer_photo.call_args.kwargs
    assert photo_kwargs["photo"] == "BACK_FILE_ID"
    assert len(photo_kwargs["caption"]) <= _PHOTO_CAPTION_MAX_FOR_TEST
    assert "short" in photo_kwargs["caption"]
    assert "x" * 1100 not in photo_kwargs["caption"]

    cb.message.answer.assert_awaited_once()
    back_text = cb.message.answer.call_args.args[0]
    assert back_text == "x" * 1100


# Mirror of src.handlers.review._PHOTO_CAPTION_MAX (kept local to avoid
# re-importing a private constant in the assertion above).
_PHOTO_CAPTION_MAX_FOR_TEST = 1000
