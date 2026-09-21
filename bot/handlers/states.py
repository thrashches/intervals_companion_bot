from aiogram.fsm.state import State, StatesGroup


class ConnectStates(StatesGroup):
    waiting_for_key = State()


class SettingsStates(StatesGroup):
    waiting_for_time = State()
    waiting_for_analysis_time = State()
    waiting_for_timezone = State()
