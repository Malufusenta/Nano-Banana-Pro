"""FSM-состояния для generation handlers (вынесено для избежания циклических импортов)."""
from aiogram.fsm.state import State, StatesGroup


class GenState(StatesGroup):
    waiting_for_category_input = State()
    waiting_for_caption = State()
    waiting_for_base_image = State()
    waiting_for_ref_image = State()
    waiting_for_replace_object_text = State()
    free_mode = State()
    waiting_for_ratio = State()
    preflight_check = State()
    selecting_ratio = State()
    waiting_for_edit_instruction = State()
    retry_waiting_photos = State()
    waiting_for_video_source = State()
    waiting_for_prompt_text = State()
    waiting_for_prompt_photo = State()
