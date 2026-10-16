from app.bot.routers.start import WELCOME_TEXT, start_menu


def test_start_menu_is_hidden_from_regular_users() -> None:
    assert start_menu(user_id=100, admin_ids=(200,)) is None


def test_start_menu_is_available_to_administrators() -> None:
    keyboard = start_menu(user_id=200, admin_ids=(200,))

    assert keyboard is not None
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }
    assert callbacks == {
        "admin:stats",
        "admin:users",
        "admin:downloads",
        "admin:channels",
        "admin:monitoring",
        "admin:system",
    }


def test_welcome_text_names_only_supported_platforms() -> None:
    assert "YouTube" in WELCOME_TEXT
    assert "TikTok" in WELCOME_TEXT
    assert "Instagram" in WELCOME_TEXT
    assert "Pinterest" in WELCOME_TEXT
    assert "VK" in WELCOME_TEXT
