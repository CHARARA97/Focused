from __future__ import annotations

import json
import re

from .script import INIT, SHARED
from .shell import HEAD, HEADER, NAV
from .style import CSS
from .tabs import ORDER, TAB_MODULES
from .text import TEXT

TOKEN = re.compile(r"\[\[([\w.]+)\]\]")


def text(key: str) -> str:
    return TEXT[key]


def _fill(markup: str) -> str:
    return TOKEN.sub(lambda match: text(match.group(1)), markup)


def _script_text() -> str:
    return (
        "const TEXT = %s;\n"
        "const T = (key) => "
        "Object.prototype.hasOwnProperty.call(TEXT, key) ? TEXT[key] : key;\n"
        % json.dumps(TEXT, ensure_ascii=False)
    )


def render() -> str:
    parts = [
        HEAD,
        "<style>\n",
        CSS,
        "</style>\n</head>\n<body>\n<header>\n",
        HEADER,
        "\n</header>\n\n<main>\n  <nav>\n",
        NAV,
        "\n  </nav>\n\n",
    ]
    for tab in ORDER:
        module = TAB_MODULES[tab]
        parts.extend([module.OPENING, "\n", module.SECTION, "\n  </section>\n\n"])

    parts.extend(["</main>\n\n<script>\n", _script_text(), SHARED, "\n"])
    for tab in ORDER:
        script = TAB_MODULES[tab].SCRIPT
        if script.strip():
            parts.extend([script, "\n"])
    parts.extend([INIT, "\n</script>\n</body>\n</html>\n"])
    return _fill("".join(parts))
