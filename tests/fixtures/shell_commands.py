"""The simple commands in a shell text, split the way bash splits them (M1-PR8, round 4).

Not a shell: enough of bash's grammar for the nightly write scan to say which
program each simple command starts and with which words. Quotes are removed,
a backslash escapes, ``\\<newline>`` continues a line, ``#`` starts a comment
at a word's start, and the control operators (``;`` ``&`` ``|`` ``&&`` ``||``
``(`` ``)`` and a newline) end a command. A redirection is no word: the file a
``<`` reads, the text of a ``<<<`` and a ``<<`` here-document are kept on the
command. The commands inside ``$( )``, backquotes and ``<( )`` are commands
too (``substituted``), and the word that holds one keeps ``$(...)`` in its
place, so it never reads as a file name. Each command records the ``( )``
subshells that hold it (``scope``, one id per subshell), so a ``cd`` inside
one ends with it. Text this module cannot split raises ``ShellSyntaxError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ShellSyntaxError(ValueError):
    """Shell text this module cannot split: the scan fails rather than guess."""


@dataclass
class Command:
    words: list[str] = field(default_factory=list)
    stdin: str | None = None  # the file a ``<`` redirect reads
    herestring: str | None = None  # the word a ``<<<`` feeds
    heredoc: bool = False  # a ``<<`` here-document feeds it
    piped_in: bool = False  # a ``|`` before it
    pipes_out: bool = False  # a ``|`` after it
    scope: tuple[int, ...] = (0,)  # the ``( )`` subshells around it, outermost first, each its own id
    substituted: bool = False  # it runs inside a ``$( )``, backquotes or ``<( )``


_REDIRECTS = ("<<<", "<<-", "<<", "<>", "<&", ">>", ">&", ">|", "&>>", "&>", "<", ">")


class _Splitter:
    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.commands: list[Command] = []
        self.inner: list[Command] = []  # from substitutions, after the command that holds them
        self.command = Command()
        self.word: list[str] = []
        self.in_word = False  # a quote opens a word even when it is empty
        self.redirect: str | None = None
        self.scope = [0]
        self.subshells = 0

    # -------------------------------------------------------------- words and commands

    def end_word(self) -> None:
        if not self.in_word:
            return
        word = "".join(self.word)
        self.word, self.in_word = [], False
        if self.redirect is None:
            self.command.words.append(word)
            return
        if self.redirect == "<":
            self.command.stdin = word
        elif self.redirect == "<<<":
            self.command.herestring = word
        elif self.redirect in ("<<", "<<-"):
            self.command.heredoc = True
        self.redirect = None

    def end_command(self, *, pipe: bool = False) -> None:
        self.end_word()
        if self.redirect is not None:
            raise ShellSyntaxError(f"a {self.redirect} redirect with no target at {self.i}")
        self.command.pipes_out = pipe
        self.commands.append(self.command)
        self.commands.extend(self.inner)
        self.inner = []
        self.command = Command(piped_in=pipe, scope=tuple(self.scope))

    # -------------------------------------------------------------- balanced spans

    def _closing(self, i: int, opening: str, closing: str) -> int:
        """The index of the ``closing`` that balances the ``opening`` just before ``i``."""

        depth, text = 1, self.text
        while i < len(text):
            char = text[i]
            if char == "\\":
                i += 2
                continue
            if char == "'":
                end = text.find("'", i + 1)
                if end < 0:
                    raise ShellSyntaxError(f"unterminated ' at {i}")
                i = end + 1
                continue
            if char == '"':
                i = self._double_quote_end(i + 1)
                continue
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        raise ShellSyntaxError(f"unclosed {opening} at {i}")

    def _double_quote_end(self, i: int) -> int:
        text = self.text
        while i < len(text):
            if text[i] == "\\":
                i += 2
            elif text[i] == '"':
                return i + 1
            elif text.startswith("$(", i):
                i = self._closing(i + 2, "(", ")") + 1
            else:
                i += 1
        raise ShellSyntaxError('unterminated "')

    def substitution(self, start: int, end: int) -> None:
        """The commands of ``text[start:end]`` run too; the word keeps a placeholder."""

        for command in commands(self.text[start:end]):
            command.substituted = True
            self.inner.append(command)
        self.word.append("$(...)")
        self.in_word = True

    def dollar(self) -> bool:
        """``$(``, ``$((`` and ``${``: True when one was read at ``self.i``."""

        text, i = self.text, self.i
        if text.startswith("$((", i):
            end = self._closing(i + 3, "(", ")")
            self.word.append(text[i : end + 2])
            self.i, self.in_word = end + 2, True
        elif text.startswith("$(", i):
            end = self._closing(i + 2, "(", ")")
            self.substitution(i + 2, end)
            self.i = end + 1
        elif text.startswith("${", i):
            end = self._closing(i + 2, "{", "}")
            self.word.append(text[i : end + 1])
            self.i, self.in_word = end + 1, True
        else:
            return False
        return True

    def backquote(self) -> None:
        end = self.text.find("`", self.i + 1)
        if end < 0:
            raise ShellSyntaxError(f"unterminated ` at {self.i}")
        self.substitution(self.i + 1, end)
        self.i = end + 1

    # -------------------------------------------------------------- the main loop

    def double_quoted(self) -> None:
        text = self.text
        self.i += 1
        self.in_word = True
        while self.i < len(text):
            char = text[self.i]
            if char == '"':
                self.i += 1
                return
            if char == "\\" and self.i + 1 < len(text):
                follow = text[self.i + 1]
                if follow != "\n":
                    self.word.append(follow if follow in '"\\$`' else char + follow)
                self.i += 2
            elif char == "$" and self.dollar():
                continue
            elif char == "`":
                self.backquote()
            else:
                self.word.append(char)
                self.i += 1
        raise ShellSyntaxError('unterminated "')

    def operator(self) -> bool:
        """A redirection or a control operator at ``self.i``: True when one was read."""

        text, i = self.text, self.i
        redirect = next((each for each in _REDIRECTS if text.startswith(each, i)), None)
        if redirect is not None and text.startswith("(", i + len(redirect)) and redirect in ("<", ">"):
            end = self._closing(i + 2, "(", ")")  # <( ) and >( ): a process substitution
            self.end_word()
            self.substitution(i + 2, end)
            self.end_word()
            self.i = end + 1
            return True
        if redirect is not None:
            if self.in_word and "".join(self.word).isdigit():
                self.word, self.in_word = [], False  # the descriptor a redirect names
            self.end_word()
            self.redirect = redirect
            self.i += len(redirect)
            return True
        for control in ("&&", "||", ";;", "|&", ";", "&", "|", "(", ")", "\n"):
            if text.startswith(control, i):
                if control == "(":
                    self.subshells += 1
                    self.scope.append(self.subshells)
                elif control == ")" and len(self.scope) > 1:  # a case pattern's ``)`` closes none
                    self.scope.pop()
                self.end_command(pipe=control in ("|", "|&"))
                self.i += len(control)
                return True
        return False

    def split(self) -> list[Command]:
        text = self.text
        while self.i < len(text):
            char = text[self.i]
            if text.startswith("\\\n", self.i):
                self.i += 2
            elif char == "\\":
                if self.i + 1 >= len(text):
                    raise ShellSyntaxError("a trailing backslash")
                self.word.append(text[self.i + 1])
                self.in_word = True
                self.i += 2
            elif char == "'":
                end = text.find("'", self.i + 1)
                if end < 0:
                    raise ShellSyntaxError(f"unterminated ' at {self.i}")
                self.word.append(text[self.i + 1 : end])
                self.in_word = True
                self.i = end + 1
            elif char == '"':
                self.double_quoted()
            elif char == "$" and self.dollar():
                continue
            elif char == "`":
                self.backquote()
            elif char == "#" and not self.in_word:
                end = text.find("\n", self.i)
                self.i = len(text) if end < 0 else end
            elif char in " \t":
                self.end_word()
                self.i += 1
            elif self.operator():
                continue
            else:
                self.word.append(char)
                self.in_word = True
                self.i += 1
        self.end_command()
        return [command for command in self.commands if command.words or command.stdin or command.herestring is not None or command.heredoc]


def commands(text: str) -> list[Command]:
    """Every simple command in ``text``, those inside substitutions included."""

    return _Splitter(text).split()
