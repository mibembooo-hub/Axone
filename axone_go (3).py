#!/usr/bin/env python3
"""AXONE GO — Simple self-contained CLI module manager."""
from __future__ import annotations
import ast, hashlib, json, re, sys, textwrap, time
from pathlib import Path
from typing import Optional

# ─── Colour helpers ───────────────────────────────────────────────────────────
def _c(code: str, t: str) -> str:
    return f"\033[{code}m{t}\033[0m" if sys.stdout.isatty() else t
def ok(t):   return _c("32", t)
def err(t):  return _c("31", t)
def info(t): return _c("36", t)
def warn(t): return _c("33", t)
def dim(t):  return _c("2",  t)
def bold(t): return _c("1",  t)

# ─── Security ─────────────────────────────────────────────────────────────────
FORBIDDEN_NAMES = frozenset({"eval","exec","compile","__import__","breakpoint"})
FORBIDDEN_ATTRS = frozenset({"__subclasses__","__bases__","__mro__","__globals__",
                              "__builtins__","__code__","__closure__"})
FORBIDDEN_MODS  = frozenset({"subprocess","pty","ctypes","cffi"})

def validate_code(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]
    errors: list[str] = []
    class V(ast.NodeVisitor):
        def visit_Import(self, n):
            for a in n.names:
                if a.name.split(".")[0] in FORBIDDEN_MODS:
                    errors.append(f"Forbidden import: {a.name}")
            self.generic_visit(n)
        def visit_ImportFrom(self, n):
            if n.module and n.module.split(".")[0] in FORBIDDEN_MODS:
                errors.append(f"Forbidden from-import: {n.module}")
            self.generic_visit(n)
        def visit_Name(self, n):
            if n.id in FORBIDDEN_NAMES:
                errors.append(f"Forbidden name: {n.id}")
            self.generic_visit(n)
        def visit_Attribute(self, n):
            if n.attr in FORBIDDEN_ATTRS:
                errors.append(f"Forbidden attr: .{n.attr}")
            if (isinstance(n.value, ast.Name) and n.value.id == "os"
                    and n.attr in {"system","popen","execv","execve","spawn"}):
                errors.append(f"Forbidden: os.{n.attr}")
            self.generic_visit(n)
    V().visit(tree)
    return errors

def has_run_fn(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(isinstance(n, ast.FunctionDef) and n.name == "run"
               for n in ast.walk(tree))

# ─── Fallback generator ───────────────────────────────────────────────────────
PATTERNS = [
    (["remind","reminder","every hour","every minute","interval"],
     textwrap.dedent("""\
         import time
         interval = 3600
         for a in args:
             if a.isdigit(): interval = int(a); break
         msg = " ".join(a for a in args if not a.isdigit()) or "Reminder!"
         print(f"Reminder every {interval}s. Ctrl+C to stop.")
         try:
             while True:
                 print(f"[REMINDER] {msg}")
                 time.sleep(interval)
         except KeyboardInterrupt:
             print("Stopped.")""")),
    (["hello","greet"],
     textwrap.dedent("""\
         name = args[0] if args else "world"
         print(f"Hello, {name}!")""")),
    (["count","counter"],
     textwrap.dedent("""\
         n = int(args[0]) if args else 10
         for i in range(1, n + 1):
             print(i)""")),
    (["time","clock","date","now"],
     textwrap.dedent("""\
         import datetime
         now = datetime.datetime.now()
         print(f"Date: {now.strftime('%Y-%m-%d')}")
         print(f"Time: {now.strftime('%H:%M:%S')}")""")),
    (["random","dice","roll"],
     textwrap.dedent("""\
         import random
         n = int(args[0]) if args else 6
         print(f"Rolled d{n}: {random.randint(1, n)}")""")),
    (["fibonacci","fib"],
     textwrap.dedent("""\
         n = int(args[0]) if args else 10
         a, b = 0, 1
         seq = []
         for _ in range(n):
             seq.append(a); a, b = b, a + b
         print(" ".join(str(x) for x in seq))""")),
    (["list file","ls","directory","show file"],
     textwrap.dedent("""\
         import os
         target = args[0] if args else "."
         try:
             for f in sorted(os.listdir(target)): print(f)
         except FileNotFoundError:
             print(f"Not found: {target}"); return 1""")),
    (["ping","check","reachable"],
     textwrap.dedent("""\
         import socket
         host = args[0] if args else "8.8.8.8"
         port = int(args[1]) if len(args) > 1 else 53
         try:
             s = socket.create_connection((host, port), timeout=5); s.close()
             print(f"{host}:{port} reachable.")
         except OSError:
             print(f"{host}:{port} NOT reachable."); return 1""")),
]

def fallback_body(description: str) -> str:
    dl = description.lower()
    for keywords, body in PATTERNS:
        if any(k in dl for k in keywords):
            return body
    return textwrap.dedent(f"""\
        # Module: {description}
        print("Running:", {repr(description)})
        print("Args:", args)""")

# ─── Module template ──────────────────────────────────────────────────────────
TEMPLATE = '''\
"""Module: {name}
Description: {description}
Created: {created}
"""
from __future__ import annotations

def run(args: list[str]) -> int:
{body}
    return 0
'''

def build_source(name: str, description: str, body: str) -> str:
    indented = "\n".join(
        "    " + ln if ln.strip() else ""
        for ln in body.strip().splitlines()
    )
    return TEMPLATE.format(
        name=name, description=description,
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
        body=indented,
    )

# ─── Module manager ───────────────────────────────────────────────────────────
def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return re.sub(r"^_+|_+$", "", text)[:48] or "module"

class ModuleManager:
    def __init__(self, base: Path) -> None:
        self.mdir = base / "modules"
        self.mdir.mkdir(parents=True, exist_ok=True)

    def _py(self, n: str) -> Path: return self.mdir / f"{n}.py"
    def _meta(self, n: str) -> Path: return self.mdir / f"{n}.meta.json"

    def exists(self, n: str) -> bool: return self._py(n).exists()

    def save(self, name: str, desc: str, source: str) -> tuple[bool,str]:
        errs = validate_code(source)
        if errs:
            return False, "Security violation:\n" + "\n".join(f"  • {e}" for e in errs)
        if not has_run_fn(source):
            return False, "Module must define `def run(args): ...`"
        self._py(name).write_text(source, encoding="utf-8")
        self._meta(name).write_text(json.dumps({
            "name": name, "description": desc,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "hash": hashlib.sha256(source.encode()).hexdigest()[:12],
        }, indent=2), encoding="utf-8")
        return True, f"Module '{name}' saved."

    def load_meta(self, name: str) -> dict:
        try:
            return json.loads(self._meta(name).read_text(encoding="utf-8"))
        except Exception:
            return {"name": name, "description": "", "created": "", "hash": ""}

    def list_all(self) -> list[dict]:
        return [self.load_meta(p.stem) for p in sorted(self.mdir.glob("*.py"))]

    def get_source(self, name: str) -> Optional[str]:
        try: return self._py(name).read_text(encoding="utf-8")
        except Exception: return None

    def delete(self, name: str) -> tuple[bool,str]:
        if not self.exists(name):
            return False, f"Module '{name}' not found."
        self._py(name).unlink()
        self._meta(name).unlink(missing_ok=True)
        return True, f"Module '{name}' deleted."

    def run(self, name: str, args: list[str]) -> tuple[int,str]:
        import importlib.util, traceback
        src = self.get_source(name)
        if src is None:
            return 1, f"Module '{name}' not found."
        errs = validate_code(src)
        if errs:
            return 1, "Security check failed:\n" + "\n".join(f"  • {e}" for e in errs)
        spec = importlib.util.spec_from_file_location(f"_axone.{name}", self._py(name))
        if not spec or not spec.loader:
            return 1, "Cannot load module."
        mod = importlib.util.module_from_spec(spec)
        t0 = time.monotonic()
        try:
            spec.loader.exec_module(mod)
            result = mod.run(args)
            elapsed = time.monotonic() - t0
            return (result if isinstance(result, int) else 0), f"Done in {elapsed:.2f}s."
        except SystemExit as e:
            return int(e.code or 0), ""
        except Exception:
            return 1, f"Runtime error:\n{traceback.format_exc(limit=6)}"

# ─── CLI ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / ".axone_go"

def _pq(t: str) -> str:
    t = t.strip()
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        return t[1:-1].strip()
    return t

def _help():
    print(f"""
  {bold('Commands')}
  {bold('/new "<desc>"'):<28}  Generate a new module
  {bold('/run <name> [args]'):<28}  Execute a module
  {bold('/list'):<28}  List all modules
  {bold('/delete <name>'):<28}  Delete a module
  {bold('/help'):<28}  Show this help
  {bold('/quit'):<28}  Exit
""")

def _cmd_new(mgr: ModuleManager, rest: str):
    desc = _pq(rest)
    if not desc:
        print(err("  Usage: /new \"<description>\""))
        return
    name = slugify(desc)
    if mgr.exists(name):
        print(warn(f"  Module '{name}' already exists. Delete it first."))
        return
    print(info(f"  Generating '{name}'…"))
    body = fallback_body(desc)
    source = build_source(name, desc, body)
    ok_flag, msg = mgr.save(name, desc, source)
    if ok_flag:
        print(ok(f"  ✓ {msg}"))
        print(dim(f"    Run: /run {name}"))
    else:
        print(err(f"  ✗ {msg}"))

def _cmd_run(mgr: ModuleManager, rest: str):
    parts = rest.split()
    if not parts:
        print(err("  Usage: /run <name> [args...]"))
        return
    name, args = parts[0], parts[1:]
    if not mgr.exists(name):
        print(err(f"  Module '{name}' not found. Use /list."))
        return
    print(info(f"  Running '{name}'…"))
    print(dim("  " + "─"*50))
    code, msg = mgr.run(name, args)
    print(dim("  " + "─"*50))
    if code == 0:
        print(ok(f"  ✓ {msg}"))
    else:
        print(err(f"  ✗ Exit {code}. {msg}"))

def _cmd_list(mgr: ModuleManager):
    mods = mgr.list_all()
    if not mods:
        print(dim("  No modules yet. Use /new to create one."))
        return
    print()
    print(f"  {bold('Name'):<28}  {bold('Description'):<36}  {bold('Created')}")
    print(dim("  " + "─"*74))
    for m in mods:
        print(f"  {bold(m['name']):<37}  {m['description'][:36]:<36}  {dim(m['created'][:16])}")
    print()

def _cmd_delete(mgr: ModuleManager, rest: str):
    name = rest.strip()
    if not name:
        print(err("  Usage: /delete <name>"))
        return
    ok_flag, msg = mgr.delete(name)
    print((ok if ok_flag else err)(f"  {'✓' if ok_flag else '✗'} {msg}"))

def dispatch(mgr: ModuleManager, line: str) -> bool:
    line = line.strip()
    if not line:
        return True
    if not line.startswith("/"):
        print(warn("  Commands start with /  (try /help)"))
        return True
    parts = line[1:].split(None, 1)
    cmd, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    if cmd in ("quit","exit","q"):
        print(dim("  Goodbye.")); return False
    elif cmd == "help": _help()
    elif cmd == "new":    _cmd_new(mgr, rest)
    elif cmd == "run":    _cmd_run(mgr, rest)
    elif cmd == "list":   _cmd_list(mgr)
    elif cmd == "delete": _cmd_delete(mgr, rest)
    else: print(err(f"  Unknown: /{cmd}  (try /help)"))
    return True

def main() -> int:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    mgr = ModuleManager(BASE_DIR)
    print(bold("\n  AXONE GO v1.0"))
    print(dim("  Type /help for commands, /quit to exit.\n"))
    if len(sys.argv) > 1:
        dispatch(mgr, " ".join(sys.argv[1:]))
        return 0
    try:
        while True:
            try:
                line = input(dim("axone-go") + " > ")
            except EOFError:
                print(); break
            if not dispatch(mgr, line):
                break
    except KeyboardInterrupt:
        print(dim("\n  Interrupted."))
    return 0

if __name__ == "__main__":
    sys.exit(main())
