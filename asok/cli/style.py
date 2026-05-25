from __future__ import annotations


class Style:
    """ANSI color styles and utility methods for professional terminal output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @staticmethod
    def success(msg: str) -> None:
        """Print a success message with a green checkmark."""
        print(f"  {Style.GREEN}✅ {msg}{Style.RESET}")

    @staticmethod
    def info(msg: str) -> None:
        """Print an informational message with a cyan icon."""
        print(f"  {Style.CYAN}ℹ️ {msg}{Style.RESET}")

    @staticmethod
    def warn(msg: str) -> None:
        """Print a warning message with a yellow icon."""
        print(f"  {Style.YELLOW}⚠ {msg}{Style.RESET}")

    @staticmethod
    def error(msg: str) -> None:
        """Print an error message with a red icon."""
        print(f"  {Style.RED}✖ {msg}{Style.RESET}")

    @staticmethod
    def heading(msg: str) -> None:
        """Print a bold blue heading."""
        print(f"\n{Style.BOLD}{Style.BLUE}{msg}{Style.RESET}")

    @staticmethod
    def confirm(question: str, default: bool = False) -> bool:
        """Ask a Y/n question interactively and return the boolean response."""
        hint = " [Y/n]" if default else " [y/N]"
        try:
            ans = (
                input(
                    f"  {Style.BOLD}{Style.CYAN}?{Style.RESET} {question}{Style.DIM}{hint}{Style.RESET}: "
                )
                .strip()
                .lower()
            )
            if not ans:
                return default
            return ans in ("y", "yes")
        except (KeyboardInterrupt, EOFError):
            print()
            return default
