"""Post link + {value}: альбом из 2+ фото в param-flow."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, PhotoSize, User

from app.handlers.generation import (
    apply_value_to_main_prompt,
    enter_broadcast_generation_preflight,
    handle_album_input,
    handle_waiting_for_prompt_text_answer,
)
from app.handlers.generation_states import GenState


def _mock_photo_message(
    *,
    message_id: int = 10,
    file_id: str = "fid_a",
    media_group_id: str | None = "mg1",
) -> MagicMock:
    user = User(id=1, is_bot=False, first_name="T")
    chat = Chat(id=1, type="private")
    photo = PhotoSize(
        file_id=file_id,
        file_unique_id=f"uniq_{file_id}",
        width=100,
        height=100,
    )
    msg = MagicMock()
    msg.message_id = message_id
    msg.media_group_id = media_group_id
    msg.from_user = user
    msg.chat = chat
    msg.photo = [photo]
    msg.answer = AsyncMock()
    msg.bot = AsyncMock()
    return msg


class PostlinkAlbumFlowTest(unittest.IsolatedAsyncioTestCase):
    async def _fsm(self, *, state: str | None, data: dict | None = None) -> FSMContext:
        storage = MemoryStorage()
        key = MagicMock(user_id=1, chat_id=1, bot_id=999)
        ctx = FSMContext(storage=storage, key=key)
        if data:
            await ctx.update_data(**data)
        if state:
            await ctx.set_state(state)
        return ctx

    def test_apply_value_substitutes_placeholder(self) -> None:
        out = apply_value_to_main_prompt("scene {value} 4k", "  beach  ")
        self.assertEqual(out, "scene beach 4k")

    async def test_enter_preflight_collects_multiple_file_ids(self) -> None:
        state = await self._fsm(state=None)
        msg = _mock_photo_message(media_group_id=None)
        bot = AsyncMock()

        urls = iter(["https://a", "https://b"])

        async def fake_url(_bot, fid: str) -> str:
            return next(urls)

        with patch(
            "app.handlers.generation.get_photo_url",
            side_effect=fake_url,
        ):
            await enter_broadcast_generation_preflight(
                msg,
                state,
                bot,
                prompt="test {value}",
                ratio="1:1",
                model="nb2",
                file_ids=["fid1", "fid2"],
            )

        data = await state.get_data()
        self.assertEqual(data["pf_image_urls"], ["https://a", "https://b"])
        self.assertEqual(await state.get_state(), GenState.preflight_check.state)
        msg.answer.assert_awaited_once()

    async def test_album_in_waiting_for_prompt_text_caches_both_ids(self) -> None:
        state = await self._fsm(
            state=GenState.waiting_for_prompt_text.state,
            data={
                "param_question_text": "Какой фон?",
                "param_main_prompt_template": "portrait {value}",
            },
        )
        msg1 = _mock_photo_message(message_id=1, file_id="p1")
        msg2 = _mock_photo_message(message_id=2, file_id="p2")
        bot = AsyncMock()

        with patch(
            "app.handlers.generation.send_param_prompt_photo_before_text_error",
            new_callable=AsyncMock,
        ) as send_err:
            await handle_album_input(msg1, state, bot, album=[msg1, msg2])

        data = await state.get_data()
        self.assertEqual(data["pending_param_photo_file_ids"], ["p1", "p2"])
        self.assertEqual(data["pending_param_photo_file_id"], "p1")
        send_err.assert_awaited_once()

    async def test_text_answer_after_album_uses_all_cached_photos(self) -> None:
        state = await self._fsm(
            state=GenState.waiting_for_prompt_text.state,
            data={
                "param_main_prompt_template": "blend {value}",
                "broadcast_ratio": "1:1",
                "broadcast_model": "nb2",
                "pending_param_photo_file_ids": ["p1", "p2"],
            },
        )
        msg = MagicMock()
        msg.text = "together"
        msg.from_user = User(id=1, is_bot=False, first_name="T")
        msg.answer = AsyncMock()
        msg.bot = AsyncMock()

        with (
            patch(
                "app.handlers.generation.offer_video_if_requested",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "app.handlers.generation.enter_broadcast_generation_preflight",
                new_callable=AsyncMock,
            ) as enter_pf,
        ):
            await handle_waiting_for_prompt_text_answer(msg, state)

        enter_pf.assert_awaited_once()
        kwargs = enter_pf.call_args.kwargs
        self.assertEqual(kwargs["prompt"], "blend together")
        self.assertEqual(kwargs["file_ids"], ["p1", "p2"])

    async def test_album_in_waiting_for_prompt_photo_broadcast_preflight(self) -> None:
        state = await self._fsm(
            state=GenState.waiting_for_prompt_photo.state,
            data={
                "from_broadcast": True,
                "broadcast_prompt": "swap faces",
                "broadcast_ratio": "3:4",
                "broadcast_model": "pro",
            },
        )
        msg1 = _mock_photo_message(message_id=1, file_id="a")
        msg2 = _mock_photo_message(message_id=2, file_id="b")
        bot = AsyncMock()

        with patch(
            "app.handlers.generation.get_photo_url",
            side_effect=lambda _b, fid: f"url_{fid}",
        ):
            await handle_album_input(msg1, state, bot, album=[msg1, msg2])

        data = await state.get_data()
        self.assertEqual(await state.get_state(), GenState.preflight_check.state)
        self.assertEqual(data["pf_image_urls"], ["url_a", "url_b"])
        self.assertEqual(data["pf_prompt"], "swap faces")


if __name__ == "__main__":
    unittest.main()
