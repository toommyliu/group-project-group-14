#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "THIRD_PARTY_LICENSES"
CMU_FONT_FILES = (
    ROOT / "assets" / "fonts" / "cmu" / "cmunrm.ttf",
    ROOT / "assets" / "fonts" / "cmu" / "cmunbx.ttf",
)
NOTICE_SEPARATOR = "------"
RUNTIME_PRUNES = ("pytest",)
PACKAGE_RE = re.compile(r"^([A-Za-z0-9_.-]+)==")
HEADER = "Our software includes the following third-party licensing:"
CMU_NOTICE = """
** Computer Modern; version 0.7.0 -- https://sourceforge.net/projects/cm-unicode/

Files:
- assets/fonts/cmu/cmunrm.ttf
- assets/fonts/cmu/cmunbx.ttf

Copyright (C) Authors of original metafont fonts:
Donald Ervin Knuth (cm, concrete fonts)
1995, 1996, 1997 J"org Knappen, 1990, 1992 Norbert Schwarz (ec fonts)
1992-2006 A.Khodulev, O.Lapko, A.Berdnikov, V.Volovich (lh fonts)
1997-2005 Claudio Beccari (cb greek fonts)
2002 FUKUI Rei (tipa fonts)
2003-2005 Han The Thanh (Vietnamese fonts)
1996-2005 Walter Schmidt (cmbright fonts)

Copyright (C) 2003-2009, Andrey V. Panov (panov@canopus.iacp.dvo.ru),
with Reserved Font Family Name "Computer Modern Unicode fonts".

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
http://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded,
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
"""


def _run(command: list[str]) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _runtime_packages() -> list[str]:
    command = [
        "uv",
        "export",
        "--locked",
        "--no-dev",
        "--no-header",
        "--no-hashes",
        "--no-emit-project",
    ]
    for package in RUNTIME_PRUNES:
        command.extend(["--prune", package])

    exported = _run(command)
    packages = {
        match.group(1).lower()
        for line in exported.splitlines()
        if (match := PACKAGE_RE.match(line.strip()))
    }
    return sorted(packages)


def _python_license_records(packages: list[str]) -> list[dict[str, Any]]:
    if not packages:
        return []

    output = _run(
        [
            "uv",
            "run",
            "--locked",
            "pip-licenses",
            "--format=json",
            "--with-license-file",
            "--with-urls",
            "--from=mixed",
            "--packages",
            *packages,
        ]
    )
    records = json.loads(output)
    return sorted(records, key=lambda record: record["Name"].casefold())


def _format_package_notice(record: dict[str, Any]) -> str:
    name = record.get("Name", "UNKNOWN")
    version = record.get("Version", "UNKNOWN")
    license_name = record.get("License", "UNKNOWN")
    url = record.get("URL", "UNKNOWN")
    license_text = _normalize_text(record.get("LicenseText") or "")

    lines = [f"** {name}; version {version} ({license_name}) -- {url}"]
    if license_text and license_text != "UNKNOWN":
        lines.extend(["", license_text])
    else:
        lines.extend(["", "License text unavailable from package metadata."])

    return "\n".join(lines)


def _bundled_asset_notices() -> list[str]:
    if any(font.exists() for font in CMU_FONT_FILES):
        return [_normalize_text(CMU_NOTICE)]
    return []


def generate() -> str:
    package_notices = [
        _format_package_notice(record)
        for record in _python_license_records(_runtime_packages())
    ]
    bundled_asset_notices = _bundled_asset_notices()

    notices = [*package_notices, *bundled_asset_notices]
    content = f"{HEADER}\n\n"
    content += f"\n\n{NOTICE_SEPARATOR}\n\n".join(notices)
    content += f"\n\n{NOTICE_SEPARATOR}\n"
    return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if THIRD_PARTY_LICENSES is not up to date",
    )
    args = parser.parse_args(argv)

    generated = generate()
    if args.check:
        current = (
            OUTPUT_FILE.read_text(encoding="utf-8")
            if OUTPUT_FILE.exists()
            else ""
        )
        current = current.replace("\r\n", "\n").replace("\r", "\n")
        if current != generated:
            print(
                "THIRD_PARTY_LICENSES is stale; run "
                "`uv run python scripts/generate_third_party_licenses.py`.",
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT_FILE.write_text(generated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
