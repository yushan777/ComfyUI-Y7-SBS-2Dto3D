"""
colored_print.py - Terminal Color Printing Module

This module provides color-enhanced terminal output by permanently overriding
the built-in print function. Print statements will use the terminal's default color
unless a specific color is provided.

Usage:
    from colored_print import color, style

    # Regular print will use terminal's default color
    print("This uses the terminal's default color")

    # Add a color code as the last argument for colored text
    print("This is red text", color.RED)

    # Use styling with colors
    print("This is bold green text", color.GREEN + style.BOLD)

    # Colors and styles are automatically reset at the end of every print

The color class provides color constants and the style class provides text formatting
constants. They can be combined using the + operator.
"""

import builtins


class color:
    BLACK = '\033[38;5;0m'
    RED = '\033[38;5;196m'
    GREEN = '\033[38;5;46m'
    YELLOW = '\033[38;5;226m'
    BLUE = '\033[38;5;21m'
    MAGENTA = '\033[38;5;201m'
    CYAN = '\033[38;5;51m'
    WHITE = '\033[38;5;15m'

    BRIGHT_BLACK = '\033[38;5;240m'
    BRIGHT_RED = '\033[38;5;203m'
    BRIGHT_GREEN = '\033[38;5;82m'
    BRIGHT_YELLOW = '\033[38;5;229m'
    BRIGHT_BLUE = '\033[38;5;75m'
    BRIGHT_MAGENTA = '\033[38;5;207m'
    BRIGHT_CYAN = '\033[38;5;159m'
    BRIGHT_WHITE = '\033[38;5;231m'

    ORANGE = '\033[38;5;208m' 
    BRIGHT_ORANGE = '\033[38;5;214m'

class style:
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'
    BLINK = '\033[5m'


# ANSI reset code (internal use only)
_RESET = '\033[0m'


# Store the original print function
_original_print = builtins.print


def _colored_print(*args, **kwargs):
    """Custom print function that handles color codes as the last argument"""
    if args and isinstance(args[-1], str) and args[-1].startswith('\033'):
        # Last arg is a color code
        color_code = args[-1]
        text_args = args[:-1]
        text = ' '.join(map(str, text_args))
        _original_print(f"{color_code}{text}{_RESET}", **kwargs)
    else:
        # No color code provided, use terminal's default color
        # Just pass through to the original print function
        _original_print(*args, **kwargs)


# Permanently override the built-in print function
builtins.print = _colored_print
