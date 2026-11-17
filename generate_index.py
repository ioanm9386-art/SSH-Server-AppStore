#!/usr/bin/env python3
"""Deterministic GitHub catalog generator. Never executes application recipes."""
import argparse, datetime, hashlib, json, os, re, subprocess
from pathlib import Path
import yaml

class UniqueLoader(yaml.SafeLoader): pass
def mapping(loader, node, deep=False):
    result = {}
    for k, v in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in result: raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(v, deep=deep)
    return result
UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
ID = re.compile(r"^[a-z][a-z0-9-]{0,48}$")
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
FIELDS = {"title", "version", "category", "tagline", "icon", "web_port", "min_ram_mb", "firewall_ports"}
def integer(v, low, high, label):
    if type(v) is not int or not low <= v <= high: raise ValueError(f"Invalid {label}")
    return v
def valid_version(value):
    core, _, build = value.partition('+')
    _, separator, pre = core.partition('-')
    if build and any(not p for p in build.split('.')): return False
    if separator:
        for p in pre.split('.'):
            if not p or (p.isdigit() and len(p)>1 and p.startswith('0')): return False
    return True
def load_app(folder):
    if folder.is_symlink() or not ID.fullmatch(folder.name): raise ValueError(f"Invalid application directory: {folder}")
    compose, icon = folder / "docker-compose.yml", folder / "icon.png"
    for f in (compose, icon):
        if f.is_symlink() or not f.is_file(): raise ValueError(f"Missing or symlinked file: {f}")
    raw = compose.read_bytes()
    if len(raw) > 256 * 1024: raise ValueError("Compose too large")
    doc = yaml.load(raw, Loader=UniqueLoader)
    if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict) or not doc["services"]: raise ValueError("Missing services")
    meta = doc.get("x-sshserver")
    if not isinstance(meta, dict) or set(meta) != FIELDS: raise ValueError(f"Invalid metadata fields: {folder.name}")
    for k, limit in [("title",100),("version",100),("category",60),("tagline",500)]:
        if not isinstance(meta[k], str) or not 0 < len(meta[k]) <= limit: raise ValueError(f"Invalid {k}")
    if not VERSION.fullmatch(meta["version"]) or not valid_version(meta["version"]): raise ValueError("version must be SemVer")
    if meta["icon"] != "icon.png": raise ValueError("icon must be icon.png in the app directory")
    integer(meta["web_port"],1,65535,"web_port"); integer(meta["min_ram_mb"],0,1048576,"min_ram_mb")
    rules = meta["firewall_ports"]
    if not isinstance(rules,list) or len(rules)>64: raise ValueError("Invalid firewall_ports")
    seen = set()
    for rule in rules:
        if not isinstance(rule,dict) or set(rule)!={"port","protocol"}: raise ValueError("Invalid firewall rule")
        integer(rule["port"],1,65535,"firewall port")
        if rule["protocol"] not in ("tcp","udp") or (rule["port"],rule["protocol"]) in seen: raise ValueError("Invalid/duplicate protocol rule")
        seen.add((rule["port"],rule["protocol"]))
    picture = icon.read_bytes()
    if len(picture)>2*1024*1024 or not picture.startswith(b"\x89PNG\r\n\x1a\n"): raise ValueError("icon.png must be a PNG up to 2 MiB")
    return {"id":folder.name, **meta, "compose_path":f"apps/{folder.name}/docker-compose.yml", "compose_sha256":hashlib.sha256(raw).hexdigest(), "icon_path":f"apps/{folder.name}/icon.png", "icon_sha256":hashlib.sha256(picture).hexdigest()}
def generate(root, repository, revision):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repository): raise ValueError("Invalid repository")
    if not re.fullmatch(r"[0-9a-f]{40}",revision): raise ValueError("Revision must be an immutable Git commit")
    folders = sorted(p for p in (root/"apps").iterdir() if p.is_dir() or p.is_symlink())
    if not 0 < len(folders) <= 1500: raise ValueError("Invalid app count")
    # Use commit time, not the current clock: identical source produces identical index.
    stamp = subprocess.check_output(["git","show","-s","--format=%cI",revision],cwd=root,text=True).strip()
    return {"schema_version":1,"repository":repository,"revision":revision,"generated_at":stamp,"apps":[load_app(p) for p in folders]}
if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--root",default=".");p.add_argument("--repository",default=os.environ.get("GITHUB_REPOSITORY"));p.add_argument("--revision",default=os.environ.get("GITHUB_SHA"));args=p.parse_args()
    root=Path(args.root).resolve();index=generate(root,args.repository,args.revision)
    output=root/"index.json";temp=output.with_suffix(".json.tmp");temp.write_text(json.dumps(index,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8");temp.replace(output)
    print(f"Generated {len(index['apps'])} apps at {args.revision}")
