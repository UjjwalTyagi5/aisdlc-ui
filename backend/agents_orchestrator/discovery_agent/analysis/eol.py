"""End-of-life status for runtimes and frameworks, and deprecated packages.

A CURATED, DATED TABLE — not a live lookup. The platform may run where outbound
calls are not allowed, and an assessment that changes because a website did is not
a planning baseline anyone can sign. Every date below is the vendor's published end
of support; update the table when a vendor publishes a new one, and the assessment
says which date it used.

Statuses, from worst to best:

  eol          past the vendor's final end of support — no security fixes
  approaching  final end of support within the next 12 months
  legacy       still patched, but frozen: no new features, and every new tool and
               library targets its successor (.NET Framework 4.7-4.8, Java 8/11
               after premier support)
  supported    in active support
  unknown      not in this table — reported as unknown, never guessed

Vulnerabilities are NOT here. They come from the scanner (Trivy) at assessment time;
a table of CVEs would be out of date the day it was written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Runtime:
    name: str
    version: str


@dataclass(frozen=True)
class RuntimeStatus:
    status: str
    eol_date: date | None
    note: str


@dataclass(frozen=True)
class _Lifecycle:
    eol: date | None          # final end of support; None = tied to an OS, no date
    legacy_after: date | None = None  # when it stopped getting anything but fixes


_APPROACHING_WINDOW = timedelta(days=365)

# ── the table ────────────────────────────────────────────────────────────────

_NET_FRAMEWORK_OS_BOUND = _Lifecycle(eol=None, legacy_after=date(2019, 4, 18))

_TABLE: dict[str, dict[str, _Lifecycle]] = {
    ".NET Framework": {
        "1.0": _Lifecycle(date(2009, 7, 14)),
        "1.1": _Lifecycle(date(2015, 10, 14)),
        "2.0": _Lifecycle(date(2011, 7, 12)),
        "3.0": _Lifecycle(date(2011, 7, 12)),
        "3.5": _Lifecycle(date(2029, 1, 9), legacy_after=date(2010, 4, 12)),
        "4.0": _Lifecycle(date(2016, 1, 12)),
        "4.5": _Lifecycle(date(2016, 1, 12)),
        "4.5.1": _Lifecycle(date(2016, 1, 12)),
        "4.5.2": _Lifecycle(date(2022, 4, 26)),
        "4.6": _Lifecycle(date(2022, 4, 26)),
        "4.6.1": _Lifecycle(date(2022, 4, 26)),
        "4.6.2": _Lifecycle(date(2027, 1, 12), legacy_after=date(2019, 4, 18)),
        "4.7": _NET_FRAMEWORK_OS_BOUND,
        "4.7.1": _NET_FRAMEWORK_OS_BOUND,
        "4.7.2": _NET_FRAMEWORK_OS_BOUND,
        "4.8": _NET_FRAMEWORK_OS_BOUND,
        "4.8.1": _NET_FRAMEWORK_OS_BOUND,
    },
    ".NET Core": {
        "1.0": _Lifecycle(date(2019, 6, 27)),
        "1.1": _Lifecycle(date(2019, 6, 27)),
        "2.0": _Lifecycle(date(2018, 10, 1)),
        "2.1": _Lifecycle(date(2021, 8, 21)),
        "2.2": _Lifecycle(date(2019, 12, 23)),
        "3.0": _Lifecycle(date(2020, 3, 3)),
        "3.1": _Lifecycle(date(2022, 12, 13)),
    },
    ".NET": {
        "5.0": _Lifecycle(date(2022, 5, 10)),
        "6.0": _Lifecycle(date(2024, 11, 12)),
        "7.0": _Lifecycle(date(2024, 5, 14)),
        "8.0": _Lifecycle(date(2026, 11, 10)),
        "9.0": _Lifecycle(date(2026, 11, 10)),
        "10.0": _Lifecycle(date(2028, 11, 14)),
    },
    ".NET Standard": {
        # A library target, not a runtime: it runs wherever a compatible runtime does.
        "1.0": _Lifecycle(None), "1.6": _Lifecycle(None),
        "2.0": _Lifecycle(None), "2.1": _Lifecycle(None),
    },
    "Java": {
        "6": _Lifecycle(date(2018, 12, 31)),
        "7": _Lifecycle(date(2022, 7, 31)),
        "8": _Lifecycle(date(2030, 12, 31), legacy_after=date(2022, 3, 31)),
        "11": _Lifecycle(date(2032, 1, 31), legacy_after=date(2023, 9, 30)),
        "17": _Lifecycle(date(2029, 9, 30), legacy_after=date(2026, 9, 30)),
        "21": _Lifecycle(date(2031, 9, 30), legacy_after=date(2028, 9, 30)),
        "25": _Lifecycle(date(2033, 9, 30), legacy_after=date(2030, 9, 30)),
    },
    "Node.js": {
        "8": _Lifecycle(date(2019, 12, 31)),
        "10": _Lifecycle(date(2021, 4, 30)),
        "12": _Lifecycle(date(2022, 4, 30)),
        "14": _Lifecycle(date(2023, 4, 30)),
        "16": _Lifecycle(date(2023, 9, 11)),
        "18": _Lifecycle(date(2025, 4, 30)),
        "19": _Lifecycle(date(2023, 6, 1)),
        "20": _Lifecycle(date(2026, 4, 30)),
        "21": _Lifecycle(date(2024, 6, 1)),
        "22": _Lifecycle(date(2027, 4, 30)),
        "23": _Lifecycle(date(2025, 6, 1)),
        "24": _Lifecycle(date(2028, 4, 30)),
    },
    "Python": {
        "2.7": _Lifecycle(date(2020, 1, 1)),
        "3.5": _Lifecycle(date(2020, 9, 13)),
        "3.6": _Lifecycle(date(2021, 12, 23)),
        "3.7": _Lifecycle(date(2023, 6, 27)),
        "3.8": _Lifecycle(date(2024, 10, 7)),
        "3.9": _Lifecycle(date(2025, 10, 31)),
        "3.10": _Lifecycle(date(2026, 10, 31)),
        "3.11": _Lifecycle(date(2027, 10, 31)),
        "3.12": _Lifecycle(date(2028, 10, 31)),
        "3.13": _Lifecycle(date(2029, 10, 31)),
    },
}

_NOTES: dict[str, str] = {
    ".NET Framework": (
        "Windows-only; 4.7-4.8.x are patched as a Windows component but frozen — "
        "every new .NET feature and most new libraries target .NET 8+."
    ),
    ".NET Standard": "A library target, not a runtime — supported wherever it runs.",
    "Java": "Dates are Oracle's; premier support ending moves a version to legacy.",
}


def _normalise_version(name: str, version: str) -> str:
    version = (version or "").strip().lstrip("vV")
    if name == "Java" and version.startswith("1."):
        version = version[2:]  # "1.8" is Java 8
    if name == "Java":
        return version.split(".")[0]
    if name == "Node.js":
        return version.split(".")[0]
    if name in {".NET", ".NET Core", ".NET Standard"} and re.fullmatch(r"\d+", version):
        return f"{version}.0"
    if name == "Python":
        return ".".join(version.split(".")[:2])
    return version


def runtime_status(runtime: Runtime | None, as_of: date) -> RuntimeStatus:
    """Where `runtime` stands on `as_of`. `unknown` for anything not in the table."""
    if runtime is None:
        return RuntimeStatus("unknown", None, "No runtime or framework version was declared.")
    versions = _TABLE.get(runtime.name)
    version = _normalise_version(runtime.name, runtime.version)
    lifecycle = versions.get(version) if versions else None
    if lifecycle is None:
        return RuntimeStatus(
            "unknown", None, f"{runtime.name} {runtime.version} is not in the lifecycle table."
        )
    note = _NOTES.get(runtime.name, "")
    if lifecycle.eol is not None and as_of > lifecycle.eol:
        return RuntimeStatus("eol", lifecycle.eol, note or "Past the vendor's end of support.")
    if lifecycle.eol is not None and lifecycle.eol - as_of <= _APPROACHING_WINDOW:
        return RuntimeStatus("approaching", lifecycle.eol, note)
    if lifecycle.legacy_after is not None and as_of > lifecycle.legacy_after:
        return RuntimeStatus("legacy", lifecycle.eol, note)
    return RuntimeStatus("supported", lifecycle.eol, note)


# ── deprecated packages ──────────────────────────────────────────────────────

#: (ecosystem, lower-cased package name or prefix ending in ".") → why.
_DEPRECATED: dict[str, dict[str, str]] = {
    ".NET": {
        "windowsazure.storage": "Deprecated by Microsoft; replaced by Azure.Storage.Blobs / Queues and Azure.Data.Tables.",
        "microsoft.azure.storage.": "Deprecated by Microsoft; replaced by the Azure.Storage.* packages.",
        "microsoft.aspnet.webapi": "ASP.NET Web API 2 runs only on .NET Framework; ASP.NET Core replaces it.",
        "microsoft.aspnet.mvc": "ASP.NET MVC 5 runs only on .NET Framework; ASP.NET Core MVC replaces it.",
        "microsoft.aspnet.signalr": "ASP.NET SignalR 2 is .NET Framework-only; ASP.NET Core SignalR replaces it.",
        "microsoft.owin": "Katana/OWIN is .NET Framework hosting; ASP.NET Core replaces it.",
        "system.data.sqlclient": "Superseded by Microsoft.Data.SqlClient.",
        "entityframework": "Entity Framework 6 is in maintenance; EF Core is the actively developed ORM.",
    },
    "Node.js": {
        "request": "Deprecated by its maintainers (2020); use fetch, undici or axios.",
        "node-sass": "Deprecated; LibSass is end-of-life — use the `sass` (Dart Sass) package.",
        "tslint": "Deprecated in favour of ESLint with typescript-eslint.",
        "moment": "In maintenance mode; the maintainers recommend Luxon, date-fns or Temporal.",
        "gulp-util": "Deprecated; its utilities were split into separate modules.",
    },
    "Java": {
        "log4j:log4j": "Log4j 1.x reached end of life in 2015; move to Log4j 2 or Logback.",
        "commons-httpclient:commons-httpclient": "End of life; replaced by Apache HttpComponents.",
    },
    "Python": {
        "nose": "Unmaintained; use pytest.",
        "pycrypto": "Unmaintained and vulnerable; use cryptography or pycryptodome.",
    },
}


def deprecated_reason(ecosystem: str, package: str) -> str | None:
    """Why `package` should not survive the migration, or None if it may."""
    table = _DEPRECATED.get(ecosystem)
    if not table:
        return None
    key = (package or "").strip().lower()
    if key in table:
        return table[key]
    for prefix, reason in table.items():
        if prefix.endswith(".") and key.startswith(prefix):
            return reason
        # A family: "microsoft.aspnet.webapi" covers "microsoft.aspnet.webapi.core".
        if key.startswith(prefix + "."):
            return reason
    return None
