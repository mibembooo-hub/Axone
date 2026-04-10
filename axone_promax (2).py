#!/usr/bin/env python3
"""AXONE PRO MAX — Production-ready self-contained CLI module manager."""
from __future__ import annotations
import ast, hashlib, importlib.util, json, logging, logging.handlers
import re, shutil, subprocess, sys, textwrap, threading, time, traceback
from pathlib import Path
from typing import Optional

# ─── Colours ──────────────────────────────────────────────────────────────────
def _c(code, t): return f"\033[{code}m{t}\033[0m" if sys.stdout.isatty() else t
def ok(t):   return _c("32", t)
def err(t):  return _c("31", t)
def info(t): return _c("36", t)
def warn(t): return _c("33", t)
def dim(t):  return _c("2",  t)
def bold(t): return _c("1",  t)

# ─── Security ─────────────────────────────────────────────────────────────────
FORBIDDEN_NAMES = frozenset({"eval", "exec", "compile", "__import__", "breakpoint"})
FORBIDDEN_ATTRS = frozenset({
    "__subclasses__", "__bases__", "__mro__", "__globals__",
    "__builtins__", "__code__", "__closure__", "__dict__",
})
FORBIDDEN_MODS = frozenset({"subprocess", "pty", "ctypes", "cffi"})


def validate_code(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]
    errors: list[str] = []

    class _V(ast.NodeVisitor):
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
                    and n.attr in {"system", "popen", "execv", "execve", "spawn"}):
                errors.append(f"Forbidden: os.{n.attr}")
            self.generic_visit(n)

        def visit_Call(self, n):
            if isinstance(n.func, ast.Attribute):
                if n.func.attr in ("__subclasses__", "__bases__"):
                    errors.append(f"Forbidden escape: .{n.func.attr}()")
            self.generic_visit(n)

    _V().visit(tree)
    return errors


def has_run_fn(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(
        isinstance(n, ast.FunctionDef) and n.name == "run"
        for n in ast.walk(tree)
    )


# ─── Logging ──────────────────────────────────────────────────────────────────
_BASE_DIR = Path.home() / ".axone_promax"
_LOG_FILE = _BASE_DIR / "axone.log"


def _setup_log() -> logging.Logger:
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("axone_promax")
    lg.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=512 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    ))
    lg.addHandler(fh)
    return lg


log = _setup_log()

# ─── Fallback generator ───────────────────────────────────────────────────────
_PATTERNS: list[tuple[list[str], str]] = [
    (
        ["remind", "reminder", "every hour", "every minute", "interval"],
        (
            "import time\n"
            "interval = 3600\n"
            "for a in args:\n"
            "    if a.isdigit():\n"
            "        interval = int(a)\n"
            "        break\n"
            'msg = " ".join(a for a in args if not a.isdigit()) or "Reminder!"\n'
            'print(f"Reminder every {interval}s. Ctrl+C to stop.")\n'
            "try:\n"
            "    while True:\n"
            '        print(f"[REMINDER] {msg}")\n'
            "        time.sleep(interval)\n"
            "except KeyboardInterrupt:\n"
            '    print("Stopped.")'
        ),
    ),
    (
        ["hello", "greet"],
        (
            'name = args[0] if args else "world"\n'
            'print(f"Hello, {name}!")'
        ),
    ),
    (
        ["count", "counter"],
        (
            "n = int(args[0]) if args else 10\n"
            "for i in range(1, n + 1):\n"
            "    print(i)"
        ),
    ),
    (
        ["time", "clock", "date", "now"],
        (
            "import datetime\n"
            "now = datetime.datetime.now()\n"
            "print(f\"Date: {now.strftime('%Y-%m-%d')}\")\n"
            "print(f\"Time: {now.strftime('%H:%M:%S')}\")"
        ),
    ),
    (
        ["random", "dice", "roll"],
        (
            "import random\n"
            "n = int(args[0]) if args else 6\n"
            'print(f"Rolled d{n}: {random.randint(1, n)}")'
        ),
    ),
    (
        ["fibonacci", "fib"],
        (
            "n = int(args[0]) if args else 10\n"
            "a, b = 0, 1\n"
            "seq = []\n"
            "for _ in range(n):\n"
            "    seq.append(a); a, b = b, a + b\n"
            'print(" ".join(str(x) for x in seq))'
        ),
    ),
    (
        ["list file", "ls", "directory", "show file"],
        (
            "import os\n"
            'target = args[0] if args else "."\n'
            "try:\n"
            "    for f in sorted(os.listdir(target)):\n"
            "        print(f)\n"
            "except FileNotFoundError:\n"
            '    print(f"Not found: {target}")\n'
            "    return 1"
        ),
    ),
    (
        ["ping", "check", "reachable"],
        (
            "import socket\n"
            'host = args[0] if args else "8.8.8.8"\n'
            "port = int(args[1]) if len(args) > 1 else 53\n"
            "try:\n"
            "    s = socket.create_connection((host, port), timeout=5)\n"
            "    s.close()\n"
            '    print(f"{host}:{port} reachable.")\n'
            "except OSError:\n"
            '    print(f"{host}:{port} NOT reachable.")\n'
            "    return 1"
        ),
    ),
]


def fallback_body(description: str) -> str:
    dl = description.lower()
    for keywords, body in _PATTERNS:
        if any(k in dl for k in keywords):
            return body
    return (
        f"# Module: {description!r}\n"
        f"print('Running:', {description!r})\n"
        "print('Args:', args)"
    )


# ─── Module template ──────────────────────────────────────────────────────────
_HDR = (
    '"""Module: {name}\nDescription: {description}\nCreated: {created}\n"""\n'
    "from __future__ import annotations\n\n\ndef run(args: list[str]) -> int:\n"
)
_FTR = "\n    return 0\n"


def build_source(name: str, description: str, body: str) -> str:
    header = _HDR.format(
        name=name,
        description=description,
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    indented = "\n".join(
        "    " + ln if ln.strip() else ""
        for ln in body.strip().splitlines()
    )
    return header + indented + _FTR


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return re.sub(r"^_+|_+$", "", text)[:48] or "module"


def _pq(t: str) -> str:
    t = t.strip()
    if (t.startswith('"') and t.endswith('"')) or (
        t.startswith("'") and t.endswith("'")
    ):
        return t[1:-1].strip()
    return t


# ─── AI Service ───────────────────────────────────────────────────────────────
class OllamaService:
    DEFAULT_MODEL   = "gemma3:4b"
    DEFAULT_TIMEOUT = 60

    def __init__(self, model: str = DEFAULT_MODEL, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.model   = model
        self.timeout = timeout
        self._avail: Optional[bool] = None

    def available(self) -> bool:
        if self._avail is None:
            self._avail = shutil.which("ollama") is not None
        return self._avail

    def _prompt(self, text: str) -> tuple[bool, str]:
        if not self.available():
            return False, "Ollama not in PATH."
        try:
            r = subprocess.run(
                ["ollama", "run", self.model],
                input=text,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode != 0:
                return False, f"Ollama error: {r.stderr.strip()}"
            return True, r.stdout.strip()
        except subprocess.TimeoutExpired:
            return False, f"Ollama timed out after {self.timeout}s."
        except FileNotFoundError:
            self._avail = False
            return False, "Ollama not found."
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _extract(text: str) -> str:
        for pat in [r"```python\s*\n(.*?)\n```", r"```\s*\n(.*?)\n```"]:
            m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return text.strip()

    def gen_body(self, description: str) -> tuple[bool, str]:
        prompt = textwrap.dedent(f"""
            You are a Python code generator for AXONE CLI.
            Task: "{description}"
            Rules:
            1. Write ONLY the body of run(args), indented 4 spaces. No def line.
            2. No eval, exec, subprocess, ctypes, pty.
            3. No os.system, os.popen, os.spawn*.
            4. Standard library only.
            5. Output ONLY a python code block.
            ```python
                # your code here
            ```
        """)
        ok_flag, raw = self._prompt(prompt)
        if not ok_flag:
            return False, raw
        code = self._extract(raw)
        return (True, code) if code else (False, "AI returned empty output.")

    def fix(self, name: str, source: str, error: str) -> tuple[bool, str]:
        prompt = textwrap.dedent(f"""
            Fix this Python module named "{name}".
            ERROR:
            {error}
            SOURCE:
            ```python
            {source}
            ```
            Rules: keep def run(args), no eval/exec/subprocess/ctypes/pty/os.system.
            Return ONLY the complete corrected Python source code.
        """)
        ok_flag, raw = self._prompt(prompt)
        if not ok_flag:
            return False, raw
        code = self._extract(raw)
        return (True, code) if code else (False, "AI returned empty fix.")


# ─── Module Manager with cache + timeout ─────────────────────────────────────
class ModuleManager:
    RUN_TIMEOUT = 30.0  # seconds

    def __init__(self, base: Path, enable_cache: bool = True) -> None:
        self.mdir  = base / "modules"
        self._cache: dict[str, object] = {}
        self._cache_enabled = enable_cache
        self.mdir.mkdir(parents=True, exist_ok=True)

    def _py(self, n: str) -> Path:
        return self.mdir / f"{n}.py"

    def _meta(self, n: str) -> Path:
        return self.mdir / f"{n}.meta.json"

    def exists(self, n: str) -> bool:
        return self._py(n).exists()

    def save(self, name: str, desc: str, source: str) -> tuple[bool, str]:
        errs = validate_code(source)
        if errs:
            log.warning("Module '%s' failed validation: %s", name, errs)
            return False, "Security violation:\n" + "\n".join(f"  • {e}" for e in errs)
        if not has_run_fn(source):
            return False, "Module must define `def run(args): ...`"
        self._py(name).write_text(source, encoding="utf-8")
        self._meta(name).write_text(
            json.dumps({
                "name": name,
                "description": desc,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "hash": hashlib.sha256(source.encode()).hexdigest()[:12],
            }, indent=2),
            encoding="utf-8",
        )
        self._cache.pop(name, None)
        log.info("Module '%s' saved", name)
        return True, f"Module '{name}' saved."

    def load_meta(self, name: str) -> dict:
        try:
            return json.loads(self._meta(name).read_text(encoding="utf-8"))
        except Exception:
            return {"name": name, "description": "", "created": "", "hash": ""}

    def list_all(self) -> list[dict]:
        return [self.load_meta(p.stem) for p in sorted(self.mdir.glob("*.py"))]

    def get_source(self, name: str) -> Optional[str]:
        try:
            return self._py(name).read_text(encoding="utf-8")
        except Exception:
            return None

    def delete(self, name: str) -> tuple[bool, str]:
        if not self.exists(name):
            return False, f"Module '{name}' not found."
        self._py(name).unlink()
        self._meta(name).unlink(missing_ok=True)
        self._cache.pop(name, None)
        log.info("Module '%s' deleted", name)
        return True, f"Module '{name}' deleted."

    def _load_module(self, name: str):
        if self._cache_enabled and name in self._cache:
            return self._cache[name], ""
        src = self.get_source(name)
        if src is None:
            return None, f"Module '{name}' not found."
        errs = validate_code(src)
        if errs:
            log.warning("Module '%s' security check failed: %s", name, errs)
            return None, "Security check:\n" + "\n".join(f"  • {e}" for e in errs)
        spec = importlib.util.spec_from_file_location(f"_axone.{name}", self._py(name))
        if not spec or not spec.loader:
            return None, "Cannot create module spec."
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            return None, f"Import error: {exc}"
        if self._cache_enabled:
            self._cache[name] = mod
        return mod, ""

    def run(self, name: str, args: list[str]) -> tuple[int, str]:
        mod, load_err = self._load_module(name)
        if mod is None:
            return 1, load_err
        if not hasattr(mod, "run") or not callable(mod.run):
            return 1, f"Module '{name}' has no callable run()."

        result_holder: list = []
        exc_holder:    list = []

        def _target() -> None:
            try:
                result_holder.append(mod.run(args))
            except SystemExit as e:
                result_holder.append(int(e.code or 0))
            except Exception:
                exc_holder.append(traceback.format_exc(limit=8))

        t0 = time.monotonic()
        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=self.RUN_TIMEOUT)
        elapsed = time.monotonic() - t0

        if thread.is_alive():
            log.error("Module '%s' timed out after %.1fs", name, elapsed)
            return 1, f"Execution timed out after {self.RUN_TIMEOUT:.0f}s."
        if exc_holder:
            log.error("Module '%s' runtime error: %s", name, exc_holder[0][:200])
            return 1, f"Runtime error:\n{exc_holder[0]}"

        code = result_holder[0] if result_holder else 0
        if not isinstance(code, int):
            code = 0
        log.info("Module '%s' exited %d in %.2fs", name, code, elapsed)
        return code, f"Done in {elapsed:.2f}s."


# ─── Onboarding ───────────────────────────────────────────────────────────────
_USER_FILE = _BASE_DIR / "user.json"


def _load_user() -> Optional[str]:
    """Return the stored user name, or None if not set yet."""
    try:
        data = json.loads(_USER_FILE.read_text(encoding="utf-8"))
        return data.get("name") or None
    except Exception:
        return None


def _save_user(name: str) -> None:
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    _USER_FILE.write_text(
        json.dumps({"name": name, "since": time.strftime("%Y-%m-%d")}, indent=2),
        encoding="utf-8",
    )


def run_onboarding() -> str:
    """
    Run first-launch onboarding. Returns the user's name.
    Skips silently if a name is already stored.
    """
    existing = _load_user()
    if existing:
        return existing

    print()
    print("  Welcome to AXONE.")
    print()
    try:
        raw = input("  Enter your name: ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    name = raw if raw else "User"
    _save_user(name)
    return name


# ─── Visual identity ──────────────────────────────────────────────────────────
_IDENTITY = r"""
   _   _  _  ___  _  _  ____
  / \ | \/ |/ _ \| \| || __|
 / _ \| \/ | (_) | .` || _|
/_/ \_|_||_|\___/|_|\_||___|
"""


def _print_header() -> None:
    print(dim(_IDENTITY))
    print(f"  {bold('Local Modular AI System')}")
    print(dim("  " + "─" * 32))


# ─── User Profile ─────────────────────────────────────────────────────────────
class UserProfile:
    _DEFAULTS: dict = {
        "username": "axone_user",
        "version": "promax",
        "ollama_model": "gemma3:4b",
        "ollama_timeout": 60,
        "modules_created": 0,
        "modules_run": 0,
        "created_at": "",
        "last_seen": "",
    }

    def __init__(self, path: Path) -> None:
        self._path = path
        self._d: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            stored = json.loads(self._path.read_text(encoding="utf-8"))
            self._d = {**self._DEFAULTS, **stored}
        except Exception:
            self._d = {**self._DEFAULTS, "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._d["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def save(self) -> None:
        self._path.write_text(json.dumps(self._d, indent=2), encoding="utf-8")

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._d.get(name, "")

    def inc(self, field: str) -> None:
        self._d[field] = self._d.get(field, 0) + 1

    def show(self) -> None:
        print()
        for k, v in self._d.items():
            print(f"  {bold(k):<28}  {v}")
        print()


# ─── Generation pipeline ──────────────────────────────────────────────────────
def generate(
    mgr: ModuleManager,
    ai: OllamaService,
    name: str,
    desc: str,
    retries: int = 2,
) -> str:
    if ai.available():
        for attempt in range(1, retries + 1):
            log.debug("AI generation attempt %d/%d for '%s'", attempt, retries, name)
            print(info(f"  AI attempt {attempt}/{retries}: asking {ai.model}…"))
            ok_flag, body = ai.gen_body(desc)
            if ok_flag:
                source = build_source(name, desc, body)
                errs = validate_code(source)
                if not errs:
                    log.info("AI generated clean module '%s'", name)
                    return source
                log.warning("AI output for '%s' has violations: %s", name, errs)
                print(warn(f"  AI code has violations — retrying…"))
            else:
                log.warning("AI gen_body failed for '%s': %s", name, body)
                print(warn(f"  AI failed: {body}"))
    else:
        log.info("Ollama offline, using fallback for '%s'", name)
        print(info("  Ollama offline — using fallback generator…"))

    print(info("  Using fallback generator…"))
    return build_source(name, desc, fallback_body(desc))


# ─── CLI ──────────────────────────────────────────────────────────────────────
def _help() -> None:
    cmds = [
        ('/new "<desc>"', "Generate a module (AI → fallback)"),
        ("/run <n> [args]", "Execute with validation, caching, timeout"),
        ("/fix <n>", "Ask AI to repair a broken module"),
        ("/list", "List all modules"),
        ("/info <n>", "Inspect module metadata and source"),
        ("/delete <n>", "Delete a module"),
        ("/profile", "Show user profile"),
        ("/log", "Show last 20 log entries"),
        ("/clear-log", "Clear the log file"),
        ("/help", "Show this help"),
        ("/quit", "Exit"),
    ]
    print()
    for cmd, desc in cmds:
        print(f"  {bold(cmd):<30}  {desc}")
    print()


def _cmd_new(
    mgr: ModuleManager, ai: OllamaService, profile: UserProfile, rest: str
) -> None:
    desc = _pq(rest)
    if not desc:
        print(err('  Usage: /new "<description>"'))
        return
    name = slugify(desc)
    if mgr.exists(name):
        print(warn(f"  Module '{name}' already exists."))
        return
    log.info("Creating module '%s': %s", name, desc)
    print(info(f"  Creating '{name}'…"))
    source = generate(mgr, ai, name, desc)
    saved, msg = mgr.save(name, desc, source)
    if saved:
        profile.inc("modules_created")
        print(ok(f"  ✓ {msg}"))
        print(dim(f"    Run: /run {name}"))
    else:
        print(err(f"  ✗ {msg}"))


def _cmd_run(
    mgr: ModuleManager, profile: UserProfile, rest: str
) -> None:
    parts = rest.split()
    if not parts:
        print(err("  Usage: /run <n> [args...]"))
        return
    name, args = parts[0], parts[1:]
    if not mgr.exists(name):
        print(err(f"  Module '{name}' not found. Use /list."))
        return
    log.info("Running '%s' with args=%s", name, args)
    print(info(f"  Running '{name}'…"))
    print(dim("  " + "─" * 54))
    code, msg = mgr.run(name, args)
    print(dim("  " + "─" * 54))
    profile.inc("modules_run")
    if code == 0:
        print(ok(f"  ✓ {msg}"))
    else:
        print(err(f"  ✗ Exit {code}. {msg}"))
        print(dim(f"  Tip: /fix {name}  or  /info {name}"))


def _cmd_fix(
    mgr: ModuleManager, ai: OllamaService, rest: str
) -> None:
    name = rest.strip()
    if not name:
        print(err("  Usage: /fix <n>"))
        return
    src = mgr.get_source(name)
    if src is None:
        print(err(f"  Module '{name}' not found."))
        return
    if not ai.available():
        print(err("  Ollama required for /fix."))
        return
    print(info("  Running to capture error…"))
    code, run_msg = mgr.run(name, [])
    if code == 0:
        print(ok("  Module runs fine — no fix needed."))
        return
    log.info("AI fix requested for '%s' — error: %s", name, run_msg[:150])
    print(info(f"  Asking {ai.model} to fix '{name}'…"))
    ok_flag, fixed = ai.fix(name, src, run_msg)
    if not ok_flag:
        log.error("AI fix failed for '%s': %s", name, fixed)
        print(err(f"  AI fix failed: {fixed}"))
        return
    meta = mgr.load_meta(name)
    saved, msg = mgr.save(name, meta.get("description", name), fixed)
    if saved:
        log.info("Module '%s' fixed and saved", name)
        print(ok(f"  ✓ Fixed. {msg}"))
        print(dim(f"  Re-run: /run {name}"))
    else:
        print(err(f"  ✗ Save failed: {msg}"))


def _cmd_list(mgr: ModuleManager) -> None:
    mods = mgr.list_all()
    if not mods:
        print(dim("  No modules yet. Use /new to create one."))
        return
    print()
    print(f"  {bold('Name'):<28}  {bold('Description'):<36}  {bold('Created')}")
    print(dim("  " + "─" * 74))
    for m in mods:
        print(
            f"  {bold(m['name']):<37}  {m['description'][:36]:<36}  "
            f"{dim(m['created'][:16])}"
        )
    print()


def _cmd_info(mgr: ModuleManager, rest: str) -> None:
    name = rest.strip()
    if not name:
        print(err("  Usage: /info <n>"))
        return
    src = mgr.get_source(name)
    if src is None:
        print(err(f"  Module '{name}' not found."))
        return
    meta = mgr.load_meta(name)
    errs = validate_code(src)
    sec_status = ok("SAFE") if not errs else err("VIOLATIONS: " + "; ".join(errs))
    run_ok = ok("yes") if has_run_fn(src) else err("MISSING")
    print()
    print(bold(f"  Module: {name}"))
    print(dim("  " + "─" * 54))
    print(f"  Description : {meta.get('description', '—')}")
    print(f"  Created     : {meta.get('created', '—')}")
    print(f"  Hash        : {meta.get('hash', '—')}")
    print(f"  Security    : {sec_status}")
    print(f"  run(args)   : {run_ok}")
    lines = src.splitlines()
    print()
    print(dim(f"  Source preview (first 20 / {len(lines)} lines):"))
    print(dim("  " + "─" * 54))
    for line in lines[:20]:
        print(f"  {line}")
    if len(lines) > 20:
        print(dim(f"  … ({len(lines) - 20} more lines)"))
    print()


def _cmd_delete(mgr: ModuleManager, rest: str) -> None:
    name = rest.strip()
    if not name:
        print(err("  Usage: /delete <n>"))
        return
    ok_flag, msg = mgr.delete(name)
    print((ok if ok_flag else err)(f"  {'✓' if ok_flag else '✗'} {msg}"))


def _cmd_log() -> None:
    if not _LOG_FILE.exists():
        print(dim("  Log file is empty."))
        return
    lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    tail  = lines[-20:] if len(lines) > 20 else lines
    print()
    for ln in tail:
        if "ERROR" in ln:
            print(err(f"  {ln}"))
        elif "WARNING" in ln:
            print(warn(f"  {ln}"))
        else:
            print(dim(f"  {ln}"))
    print()


def _cmd_clear_log() -> None:
    try:
        _LOG_FILE.write_text("", encoding="utf-8")
        log.info("Log cleared by user")
        print(ok("  ✓ Log cleared."))
    except OSError as exc:
        print(err(f"  ✗ {exc}"))


def dispatch(
    mgr: ModuleManager,
    ai: OllamaService,
    profile: UserProfile,
    line: str,
) -> bool:
    line = line.strip()
    if not line:
        return True
    if not line.startswith("/"):
        print(warn("  Commands start with /  (try /help)"))
        return True
    parts = line[1:].split(None, 1)
    cmd  = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if cmd in ("quit", "exit", "q"):
        profile.save()
        log.info("Session ended")
        print(dim("  Goodbye."))
        return False
    elif cmd == "help":
        _help()
    elif cmd == "new":
        _cmd_new(mgr, ai, profile, rest)
    elif cmd == "run":
        _cmd_run(mgr, profile, rest)
    elif cmd == "fix":
        _cmd_fix(mgr, ai, rest)
    elif cmd == "list":
        _cmd_list(mgr)
    elif cmd == "info":
        _cmd_info(mgr, rest)
    elif cmd == "delete":
        _cmd_delete(mgr, rest)
    elif cmd == "profile":
        profile.show()
    elif cmd == "log":
        _cmd_log()
    elif cmd in ("clear-log", "clearlog"):
        _cmd_clear_log()
    else:
        print(err(f"  Unknown: /{cmd}  (try /help)"))
    return True


# ─── Entry point ─────────────────────────────────────────────────────────────
def main() -> int:
    _BASE_DIR.mkdir(parents=True, exist_ok=True)

    # Visual identity — always shown
    _print_header()

    # Onboarding — runs only on first launch
    user_name = run_onboarding()

    profile = UserProfile(_BASE_DIR / "profile.json")
    ai      = OllamaService(
        model=profile.ollama_model or OllamaService.DEFAULT_MODEL,
        timeout=int(profile.ollama_timeout or OllamaService.DEFAULT_TIMEOUT),
    )
    mgr = ModuleManager(_BASE_DIR, enable_cache=True)

    log.info("AXONE PRO MAX started — user=%s", user_name)

    # Greeting
    print()
    print(f"  Welcome back, {bold(user_name)}.")
    ai_status = ok("available") if ai.available() else warn("offline (fallback active)")
    print(dim(f"  Ollama: {ai_status}"))
    print(dim(f"  Log:    {_LOG_FILE}"))
    print(dim("  Type /help for commands, /quit to exit.\n"))

    if len(sys.argv) > 1:
        dispatch(mgr, ai, profile, " ".join(sys.argv[1:]))
        profile.save()
        return 0

    try:
        while True:
            try:
                line = input(dim("axone-max") + " > ")
            except EOFError:
                print()
                break
            if not dispatch(mgr, ai, profile, line):
                break
    except KeyboardInterrupt:
        print(dim("\n  Interrupted."))
    finally:
        profile.save()
        log.info("AXONE PRO MAX session ended")

    return 0


if __name__ == "__main__":
    sys.exit(main())
