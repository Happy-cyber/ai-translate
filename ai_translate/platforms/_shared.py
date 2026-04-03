"""Shared utilities for platform handlers.

This module contains functions that are common across Django, Flask, and
FastAPI platform handlers, eliminating duplication for AST string extraction,
message filtering, and PO file management.
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import polib

log = logging.getLogger(__name__)


# ── AST string extraction ───────────────────────────────────────────────


def extract_string(node: ast.AST) -> str | None:
    """Resolve a literal string from an AST node.

    Handles the following node types:

    * ``ast.Constant`` -- plain string literals.
    * ``ast.BinOp`` with ``ast.Add`` -- string concatenation
      (e.g. ``"hello " + "world"``).  Both operands are resolved
      recursively; if either side is not a resolvable string the
      function returns ``None``.
    * ``ast.JoinedStr`` -- f-strings whose interpolated parts are
      simple variable names (e.g. ``f"hello {name}"``).  Complex
      expressions inside braces cause the function to return ``None``.

    Args:
        node: An AST node to extract a string value from.

    Returns:
        The resolved string, or ``None`` when the node cannot be
        reduced to a constant string.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = extract_string(node.left)
        right = extract_string(node.right)
        if left is not None and right is not None:
            return left + right

    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            elif isinstance(val, ast.FormattedValue) and isinstance(
                val.value, ast.Name
            ):
                parts.append(f"{{{val.value.id}}}")
            else:
                return None
        return "".join(parts)

    return None


# ── Message filtering ───────────────────────────────────────────────────


def should_skip_message(msg: str) -> bool:
    """Decide whether a message should NOT be sent for translation.

    A message is skipped when any of these conditions hold:

    * Its length is less than 2 characters.
    * It starts with a URL scheme (``http://``, ``https://``, or
      ``ftp://``).
    * It consists purely of numeric characters and whitespace (spaces,
      tabs, newlines).
    * It contains more than 6 HTML tag markers (counted as the total
      number of ``<`` and ``>`` characters).

    Args:
        msg: The candidate message string.

    Returns:
        ``True`` if the message should be skipped (i.e. *not*
        translated), ``False`` otherwise.
    """
    if len(msg) < 2:
        return True

    if msg.startswith(("http://", "https://", "ftp://")):
        return True

    if msg.replace(" ", "").replace("\t", "").replace("\n", "").isnumeric():
        return True

    if msg.count("<") + msg.count(">") > 6:
        return True

    return False


# ── PO file management ──────────────────────────────────────────────────


def atomic_save_po(po: polib.POFile, target: Path) -> None:
    """Save a PO file atomically via a temporary file and :func:`shutil.move`.

    The target's parent directories are created if they do not already
    exist.  A temporary file is written in the same directory as *target*
    so that the final :func:`shutil.move` is an atomic rename on most
    filesystems.  If anything goes wrong the temporary file is cleaned up
    before the exception propagates.

    Args:
        po: The :class:`polib.POFile` instance to persist.
        target: Destination path for the ``.po`` file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".po.tmp")
    try:
        os.close(fd)
        po.save(tmp)
        shutil.move(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_po(path: Path, lang_code: str) -> polib.POFile:
    """Load an existing PO file or create a new one with standard metadata.

    If *path* points to an existing file it is loaded with
    :func:`polib.pofile`.  When the file is missing or corrupt a fresh
    :class:`polib.POFile` is returned with ``Content-Type``,
    ``Content-Transfer-Encoding``, and ``Language`` metadata already set.

    Args:
        path: Filesystem path to the ``.po`` file.
        lang_code: BCP-47 / POSIX language code (e.g. ``"fr"``,
            ``"pt_BR"``).

    Returns:
        A :class:`polib.POFile` ready for appending new entries.
    """
    if path.is_file():
        try:
            return polib.pofile(str(path))
        except Exception:
            log.warning("Corrupt PO file %s; creating new.", path)

    po = polib.POFile()
    po.metadata = {
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Language": lang_code,
        "Plural-Forms": PLURAL_FORMS.get(lang_code, PLURAL_FORMS.get(lang_code.split("-")[0], "nplurals=2; plural=(n != 1);")),
    }
    return po


def compile_po_files(search_dir: Path) -> bool:
    """Compile every ``.po`` file under *search_dir* to ``.mo`` using ``msgfmt``.

    The function walks *search_dir* recursively.  For each ``.po`` file
    found it invokes ``msgfmt -o <path>.mo <path>.po``.  Individual
    compilation failures are logged as warnings but do not abort the
    remaining files.

    Args:
        search_dir: Root directory to search for ``.po`` files.

    Returns:
        ``True`` if **all** compilations succeeded (or there were no
        ``.po`` files), ``False`` if ``msgfmt`` is missing or any file
        failed to compile.
    """
    if not shutil.which("msgfmt"):
        log.warning("msgfmt not found; skipping .mo compilation.")
        return False

    success = True
    for po_file in search_dir.rglob("*.po"):
        mo_file = po_file.with_suffix(".mo")
        try:
            subprocess.run(
                ["msgfmt", "-o", str(mo_file), str(po_file)],
                capture_output=True,
                timeout=30,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            log.warning("Failed to compile %s", po_file)
            success = False

    return success


# ── Plural support ────────────────────────────────────────────────────

# Prefix used to mark plural entries in the translation pipeline.
# Format: "::plural::{singular}||{plural}" for PO-based platforms.
PLURAL_MARKER = "::plural::"

NGETTEXT_CALLS = frozenset({
    "ngettext", "ngettext_lazy", "ungettext", "ungettext_lazy", "npgettext",
})

# CLDR plural forms header per language code (most common languages).
# Used when creating new .po files.
PLURAL_FORMS: dict[str, str] = {
    "ar": "nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;",
    "bg": "nplurals=2; plural=(n != 1);",
    "bn": "nplurals=2; plural=(n != 1);",
    "ca": "nplurals=2; plural=(n != 1);",
    "cs": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    "da": "nplurals=2; plural=(n != 1);",
    "de": "nplurals=2; plural=(n != 1);",
    "el": "nplurals=2; plural=(n != 1);",
    "en": "nplurals=2; plural=(n != 1);",
    "es": "nplurals=2; plural=(n != 1);",
    "et": "nplurals=2; plural=(n != 1);",
    "fa": "nplurals=2; plural=(n > 1);",
    "fi": "nplurals=2; plural=(n != 1);",
    "fr": "nplurals=2; plural=(n > 1);",
    "he": "nplurals=2; plural=(n != 1);",
    "hi": "nplurals=2; plural=(n != 1);",
    "hr": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "hu": "nplurals=2; plural=(n != 1);",
    "id": "nplurals=1; plural=0;",
    "it": "nplurals=2; plural=(n != 1);",
    "ja": "nplurals=1; plural=0;",
    "ko": "nplurals=1; plural=0;",
    "lt": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "lv": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n != 0 ? 1 : 2);",
    "ms": "nplurals=1; plural=0;",
    "nb": "nplurals=2; plural=(n != 1);",
    "nl": "nplurals=2; plural=(n != 1);",
    "no": "nplurals=2; plural=(n != 1);",
    "pl": "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "pt": "nplurals=2; plural=(n != 1);",
    "pt-BR": "nplurals=2; plural=(n > 1);",
    "ro": "nplurals=3; plural=(n==1 ? 0 : (n==0 || (n%100>0 && n%100<20)) ? 1 : 2);",
    "ru": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "sk": "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    "sl": "nplurals=4; plural=(n%100==1 ? 0 : n%100==2 ? 1 : n%100==3 || n%100==4 ? 2 : 3);",
    "sr": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "sv": "nplurals=2; plural=(n != 1);",
    "th": "nplurals=1; plural=0;",
    "tr": "nplurals=2; plural=(n > 1);",
    "uk": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "vi": "nplurals=1; plural=0;",
    "zh": "nplurals=1; plural=0;",
    "zh-Hans": "nplurals=1; plural=0;",
    "zh-Hant": "nplurals=1; plural=0;",
    "zh-TW": "nplurals=1; plural=0;",
}


def get_nplurals(lang_code: str) -> int:
    """Return the number of plural forms for a language code."""
    form = PLURAL_FORMS.get(lang_code, PLURAL_FORMS.get(lang_code.split("-")[0], ""))
    if not form:
        return 2  # Default: singular + plural
    import re
    m = re.search(r"nplurals=(\d+)", form)
    return int(m.group(1)) if m else 2


def encode_plural(singular: str, plural: str) -> str:
    """Encode a singular/plural pair into a single string for the pipeline."""
    return f"{PLURAL_MARKER}{singular}||{plural}"


def decode_plural(encoded: str) -> tuple[str, str] | None:
    """Decode a plural-marked string. Returns (singular, plural) or None."""
    if not encoded.startswith(PLURAL_MARKER):
        return None
    body = encoded[len(PLURAL_MARKER):]
    parts = body.split("||", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None
