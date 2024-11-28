# mypy: disallow-untyped-defs
from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Iterator
import datetime
import pathlib
import re
from textwrap import dedent
from textwrap import indent
from typing import Any
from typing import TypedDict

import packaging.version
import platformdirs
from requests_cache import CachedResponse
from requests_cache import CachedSession
from requests_cache import OriginalResponse
from requests_cache import SQLiteCache
import tabulate
from tqdm import tqdm
import wcwidth


FILE_HEAD = r"""
.. Note this file is autogenerated by scripts/update-plugin-list.py - usually weekly via github action

.. _plugin-list:

Pytest Plugin List
==================

Below is an automated compilation of ``pytest``` plugins available on `PyPI <https://pypi.org>`_.
It includes PyPI projects whose names begin with ``pytest-`` or ``pytest_`` and a handful of manually selected projects.
Packages classified as inactive are excluded.

For detailed insights into how this list is generated,
please refer to `the update script <https://github.com/pytest-dev/pytest/blob/main/scripts/update-plugin-list.py>`_.

.. warning::

   Please be aware that this list is not a curated collection of projects
   and does not undergo a systematic review process.
   It serves purely as an informational resource to aid in the discovery of ``pytest`` plugins.

   Do not presume any endorsement from the ``pytest`` project or its developers,
   and always conduct your own quality assessment before incorporating any of these plugins into your own projects.


.. The following conditional uses a different format for this list when
   creating a PDF, because otherwise the table gets far too wide for the
   page.

"""
DEVELOPMENT_STATUS_CLASSIFIERS = (
    "Development Status :: 1 - Planning",
    "Development Status :: 2 - Pre-Alpha",
    "Development Status :: 3 - Alpha",
    "Development Status :: 4 - Beta",
    "Development Status :: 5 - Production/Stable",
    "Development Status :: 6 - Mature",
    "Development Status :: 7 - Inactive",
)
ADDITIONAL_PROJECTS = {  # set of additional projects to consider as plugins
    "logassert",
    "logot",
    "nuts",
    "flask_fixture",
    "databricks-labs-pytester",
}


def escape_rst(text: str) -> str:
    """Rudimentary attempt to escape special RST characters to appear as
    plain text."""
    text = (
        text.replace("*", "\\*")
        .replace("<", "\\<")
        .replace(">", "\\>")
        .replace("`", "\\`")
    )
    text = re.sub(r"_\b", "", text)
    return text


def project_response_with_refresh(
    session: CachedSession, name: str, last_serial: int
) -> OriginalResponse | CachedResponse:
    """Get a http cached pypi project

    force refresh in case of last serial mismatch
    """
    response = session.get(f"https://pypi.org/pypi/{name}/json")
    if int(response.headers.get("X-PyPI-Last-Serial", -1)) != last_serial:
        response = session.get(f"https://pypi.org/pypi/{name}/json", refresh=True)
    return response


def get_session() -> CachedSession:
    """Configures the requests-cache session"""
    cache_path = platformdirs.user_cache_path("pytest-plugin-list")
    cache_path.mkdir(exist_ok=True, parents=True)
    cache_file = cache_path.joinpath("http_cache.sqlite3")
    return CachedSession(backend=SQLiteCache(cache_file))


def pytest_plugin_projects_from_pypi(session: CachedSession) -> dict[str, int]:
    response = session.get(
        "https://pypi.org/simple",
        headers={"Accept": "application/vnd.pypi.simple.v1+json"},
        refresh=True,
    )
    return {
        name: p["_last-serial"]
        for p in response.json()["projects"]
        if (
            (name := p["name"]).startswith(("pytest-", "pytest_"))
            or name in ADDITIONAL_PROJECTS
        )
    }


class PluginInfo(TypedDict):
    """Relevant information about a plugin to generate the summary."""

    name: str
    summary: str
    last_release: str
    status: str
    requires: str


def iter_plugins() -> Iterator[PluginInfo]:
    session = get_session()
    name_2_serial = pytest_plugin_projects_from_pypi(session)

    for name, last_serial in tqdm(name_2_serial.items(), smoothing=0):
        response = project_response_with_refresh(session, name, last_serial)
        if response.status_code == 404:
            # Some packages, like pytest-azurepipelines42, are included in https://pypi.org/simple
            # but return 404 on the JSON API. Skip.
            continue
        response.raise_for_status()
        info = response.json()["info"]
        if "Development Status :: 7 - Inactive" in info["classifiers"]:
            continue
        for classifier in DEVELOPMENT_STATUS_CLASSIFIERS:
            if classifier in info["classifiers"]:
                status = classifier[22:]
                break
        else:
            status = "N/A"
        requires = "N/A"
        if info["requires_dist"]:
            for requirement in info["requires_dist"]:
                if re.match(r"pytest(?![-.\w])", requirement):
                    requires = requirement
                    break

        def version_sort_key(version_string: str) -> Any:
            """
            Return the sort key for the given version string
            returned by the API.
            """
            try:
                return packaging.version.parse(version_string)
            except packaging.version.InvalidVersion:
                # Use a hard-coded pre-release version.
                return packaging.version.Version("0.0.0alpha")

        releases = response.json()["releases"]
        for release in sorted(releases, key=version_sort_key, reverse=True):
            if releases[release]:
                release_date = datetime.date.fromisoformat(
                    releases[release][-1]["upload_time_iso_8601"].split("T")[0]
                )
                last_release = release_date.strftime("%b %d, %Y")
                break
        name = f':pypi:`{info["name"]}`'
        summary = ""
        if info["summary"]:
            summary = escape_rst(info["summary"].replace("\n", ""))
        yield {
            "name": name,
            "summary": summary.strip(),
            "last_release": last_release,
            "status": status,
            "requires": requires,
        }


def plugin_definitions(plugins: Iterable[PluginInfo]) -> Iterator[str]:
    """Return RST for the plugin list that fits better on a vertical page."""
    for plugin in plugins:
        yield dedent(
            f"""
            {plugin['name']}
               *last release*: {plugin["last_release"]},
               *status*: {plugin["status"]},
               *requires*: {plugin["requires"]}

               {plugin["summary"]}
            """
        )


def main() -> None:
    plugins = [*iter_plugins()]

    reference_dir = pathlib.Path("doc", "en", "reference")

    plugin_list = reference_dir / "plugin_list.rst"
    with plugin_list.open("w", encoding="UTF-8") as f:
        f.write(FILE_HEAD)
        f.write(f"This list contains {len(plugins)} plugins.\n\n")
        f.write(".. only:: not latex\n\n")

        _ = wcwidth  # reference library that must exist for tabulate to work
        plugin_table = tabulate.tabulate(plugins, headers="keys", tablefmt="rst")
        f.write(indent(plugin_table, "   "))
        f.write("\n\n")

        f.write(".. only:: latex\n\n")
        f.write(indent("".join(plugin_definitions(plugins)), "  "))


if __name__ == "__main__":
    main()
