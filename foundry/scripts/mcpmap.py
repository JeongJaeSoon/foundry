"""Generic MCP server names in agent `tools:` lists, mapped to each install's real servers.

Agent files name generic servers (`mcp__slack__slack_read_thread`). A user whose servers carry those names needs
nothing. Anyone else gets a mapping: prefs `mcp_servers` ({generic: [server, ...]}), else discovery: a server the
install has configured (user MCP config or an enabled plugin's `.mcp.json`) whose name ends in `-<generic>` or
`_<generic>`, when exactly one does. The board-pane module re-registers each affected agent at session start with
the mapped names (`foundry mcp specs`); the launcher adds the mapped names to the lead ask rules.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_RE = re.compile(r"^mcp__([A-Za-z0-9_-]+?)__(.+)$")
SERVER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Agent frontmatter fields an agent definition carries over to `$.agent.register` as they are.
SPEC_FIELDS = ("model", "effort", "color")


def valid_server(name):
    return isinstance(name, str) and bool(SERVER_RE.match(name)) and "__" not in name


def _frontmatter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    head, body = text.split("\n---\n", 1) if text.startswith("---\n") else ("", text)
    meta = {}
    for line in head.splitlines()[1:]:
        k, sep, v = line.partition(":")
        if sep and re.fullmatch(r"[A-Za-z]+", k.strip()):
            meta[k.strip()] = v.strip()
    return meta, body.lstrip("\n")


def _split(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def agents(root=ROOT):
    """{name: (meta, body)} for the plugin's agent files."""
    d = os.path.join(root, "agents")
    out = {}
    for n in sorted(os.listdir(d)):
        if n.endswith(".md"):
            meta, body = _frontmatter(os.path.join(d, n))
            out[meta.get("name") or n[:-3]] = (meta, body)
    return out


def generic_servers(root=ROOT):
    """Servers the agent files name in `tools:` (exact names, not `*` wildcards' servers)."""
    out = set()
    for meta, _ in agents(root).values():
        for t in _split(meta.get("tools")):
            m = TOOL_RE.match(t)
            if m:
                out.add(m.group(1))
    return out


def _config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def installed_servers():
    """Server names as their tools are prefixed: user MCP config servers, and `plugin_<plugin>_<server>` for every
    enabled plugin's `.mcp.json`. FOUNDRY_MCP_SERVERS (JSON list) replaces the scan in tests."""
    fake = os.environ.get("FOUNDRY_MCP_SERVERS")
    if fake:
        return set(json.loads(fake))
    # A session may run under CLAUDE_CONFIG_DIR while the user's servers stay in ~/.claude.json: read both.
    dirs = list(dict.fromkeys([_config_dir(), os.path.expanduser("~/.claude")]))
    names = set()
    for cfg in dict.fromkeys([os.path.join(d, ".claude.json") for d in dirs] + [os.path.expanduser("~/.claude.json")]):
        data = _load(cfg)
        for b in [data.get("mcpServers")] + [(v or {}).get("mcpServers") for v in (data.get("projects") or {}).values()
                                             if isinstance(v, dict)]:
            if isinstance(b, dict):
                names |= set(b)
    enabled, plugins = set(), {}
    for d in dirs:
        enabled |= {k for k, v in (_load(os.path.join(d, "settings.json")).get("enabledPlugins") or {}).items() if v}
        plugins = {**(_load(os.path.join(d, "plugins", "installed_plugins.json")).get("plugins") or {}), **plugins}
    for key, entries in plugins.items():
        if key not in enabled:
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            servers = (_load(os.path.join((e or {}).get("installPath") or "", ".mcp.json")).get("mcpServers") or {})
            names |= {f"plugin_{key.split('@', 1)[0]}_{s}" for s in servers}
    return {re.sub(r"[^A-Za-z0-9_-]", "_", n) for n in names}


def discover(generics, servers):
    """({generic: server} for a unique `-g`/`_g` suffix match, {generic: [candidates]} when ambiguous). A generic the
    install has under its own name needs no mapping."""
    found, ambiguous = {}, {}
    for g in sorted(generics):
        if g in servers:
            continue
        hits = sorted(s for s in servers if s.endswith("-" + g) or s.endswith("_" + g))
        if len(hits) == 1:
            found[g] = hits[0]
        elif hits:
            ambiguous[g] = hits
    return found, ambiguous


def mapping(p=None, servers=None):
    """{generic: [server, ...]}: prefs `mcp_servers` first, discovery for the rest."""
    if p is None:
        import prefs
        p = prefs.load()
    pref = {g: ([v] if isinstance(v, str) else list(v)) for g, v in (p.get("mcp_servers") or {}).items()}
    found, _ = discover(generic_servers() - set(pref), installed_servers() if servers is None else servers)
    return {**{g: [s] for g, s in found.items()}, **pref}


def map_tool(tool, m):
    """A tool name plus its mapped twins; anything not `mcp__<generic>__...` as it is."""
    hit = TOOL_RE.match(tool)
    if not hit or hit.group(1) not in m:
        return [tool]
    return [tool] + [f"mcp__{s}__{hit.group(2)}" for s in m[hit.group(1)] if s != hit.group(1)]


def map_tools(tools, m):
    return list(dict.fromkeys(x for t in tools for x in map_tool(t, m)))


def specs(m, root=ROOT):
    """`$.agent.register` specs for the agents whose tool lists the mapping changes: the file's own fields, prompt and
    tools, with mapped server names added."""
    out = []
    for name, (meta, body) in agents(root).items():
        tools = _split(meta.get("tools"))
        mapped = map_tools(tools, m)
        if mapped == tools:
            continue
        spec = {"name": name, "description": meta.get("description", ""), "prompt": body, "tools": mapped}
        if meta.get("disallowedTools"):
            spec["disallowedTools"] = _split(meta["disallowedTools"])
        spec.update({k: meta[k] for k in SPEC_FIELDS if meta.get(k)})
        out.append(spec)
    return out


def check(p):
    """Preflight: (ok, detail). Ambiguous discovery fails: the owner names the server in prefs `mcp_servers`."""
    servers = installed_servers()
    pref = set((p.get("mcp_servers") or {}))
    generics = generic_servers() - pref
    found, ambiguous = discover(generics, servers)
    if ambiguous:
        return False, "; ".join(f"several servers could be `{g}`: {', '.join(v)} (foundry prefs set mcp_servers.{g} "
                                f"'[\"<server>\"]')" for g, v in ambiguous.items())
    m = mapping(p, servers)
    missing = sorted(g for g in generics if g not in servers and g not in found)
    same = sorted(g for g in generics if g in servers)
    parts = [f"{g} → {', '.join(v)}" for g, v in sorted(m.items())]
    return True, "; ".join(parts + ([f"installed (same name): {', '.join(same)}"] if same else [])
                           + ([f"not installed: {', '.join(missing)}"] if missing else [])) or "no mapping needed"


AGENT_KEYS = ("description", "prompt", "tools", "disallowedTools", "model")


def agent_flag(agent_type, m=None):
    """{agent_type: definition} for `claude --agents`, with this install's server names, or {} when the mapping
    changes nothing. A --bg or interactive `--agent` main session takes its agent from the file before the board-pane
    module re-registers it (measured 2.1.329), so the foreman and every lead get the mapped definition at start."""
    short = agent_type.split(":")[-1]
    spec = next((x for x in specs(mapping() if m is None else m) if x.get("name") == short), None)
    return {agent_type: {k: spec[k] for k in AGENT_KEYS if k in spec}} if spec else {}


def cmd_mcp(a):
    if a.sub == "agent":
        try:
            print(json.dumps(agent_flag(a.agent_type), ensure_ascii=False))
        except Exception:  # the file agent stays
            print("{}")
        return 0
    if a.sub == "specs":
        try:
            out = specs(mapping())
        except Exception as e:  # the module registers nothing on error; the file agents stay
            print(json.dumps({"agents": [], "error": str(e)}))
            return 0
        print(json.dumps({"agents": out}, ensure_ascii=False))
        return 0
    import prefs
    p = prefs.load()
    ok, detail = check(p)
    print(json.dumps({"ok": ok, "detail": detail, "mapping": mapping(p)}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


def add_cli(sp):
    t = sp.add_parser("mcp", help="generic MCP server names in agent tool lists and this install's mapping")
    s = t.add_subparsers(dest="sub", required=True)
    s.add_parser("specs", help="agent specs with mapped tool names, for the board-pane module (JSON)")
    ag = s.add_parser("agent", help="the mapped definition for `claude --agents` (the foreman start line uses it)")
    ag.add_argument("agent_type", help="foundry:foreman, foundry:line-lead, ...")
    s.add_parser("map", help="show the mapping and what discovery found; exit 1 when a name is ambiguous")
    t.set_defaults(func=cmd_mcp)
