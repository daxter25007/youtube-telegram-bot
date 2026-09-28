from aiogram.fsm.state import State, StatesGroup


class AddChannelState(StatesGroup):
    waiting_for_reference = State()
    waiting_for_confirmation = State()
