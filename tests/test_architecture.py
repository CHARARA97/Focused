import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "focused"
PROJECT = {
    "domain",
    "core",
    "app",
    "api",
    "ports",
    "config",
    "engine",
    "server",
    "plugins",
    "plugin_api",
    "diagnostics",
    "ui",
}


def modules_in(directory):
    return sorted(path for path in (ROOT / directory).glob("*.py"))


def resolved_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = path.parent.relative_to(ROOT).parts
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                found.add(node.module or "")
                continue
            base = list(package[: len(package) - (node.level - 1)])
            if node.module:
                base.extend(node.module.split("."))
            found.add(".".join(base))
    return found


def project_imports(path):
    return {
        name.split(".")[0]
        for name in resolved_imports(path)
        if name.split(".")[0] in PROJECT
    }


class DependencyDirectionTests(unittest.TestCase):
    def test_domain_depends_only_on_domain(self):
        for path in modules_in("domain"):
            imports = project_imports(path)
            self.assertTrue(
                imports <= {"domain"},
                "%s imports %s" % (path.name, sorted(imports - {"domain"})),
            )

    def test_core_never_imports_the_application_or_the_transport(self):
        for path in modules_in("core"):
            imports = project_imports(path)
            forbidden = imports & {"app", "api", "server", "engine", "ui", "plugins"}
            self.assertEqual(set(), forbidden, "%s imports %s" % (path.name, forbidden))

    def test_the_app_never_imports_the_transport(self):
        for path in modules_in("app"):
            imports = project_imports(path)
            forbidden = imports & {"api", "server", "engine", "ui"}
            self.assertEqual(set(), forbidden, "%s imports %s" % (path.name, forbidden))


class ImmutabilityTests(unittest.TestCase):
    def test_every_domain_dataclass_is_frozen(self):
        for path in modules_in("domain"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for decorator in node.decorator_list:
                    if _is_dataclass(decorator) and not _is_frozen(decorator):
                        self.fail("%s.%s is mutable" % (path.name, node.name))

    def test_domain_holds_no_mutable_container_at_import_time(self):
        for path in modules_in("domain"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            self.assertNotIsInstance(
                                node.value,
                                (ast.List, ast.Dict, ast.Set),
                                "%s.%s is a mutable global" % (path.name, target.id),
                            )


class NoIoInDomainTests(unittest.TestCase):
    IO_MODULES = {"os", "subprocess", "socket", "threading", "time", "shutil", "json"}
    IO_CALLS = {"open", "kill", "system", "popen", "run", "sleep"}

    def test_domain_imports_no_io_module(self):
        for path in modules_in("domain"):
            imports = resolved_imports(path)
            offenders = {name for name in imports if name.split(".")[0] in self.IO_MODULES}
            self.assertEqual(set(), offenders, "%s imports %s" % (path.name, offenders))

    def test_domain_calls_no_io_function(self):
        for path in modules_in("domain"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(
                        node.func.id,
                        self.IO_CALLS,
                        "%s calls %s" % (path.name, node.func.id),
                    )


def _is_dataclass(decorator):
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    return isinstance(decorator, ast.Name) and decorator.id == "dataclass"


def _is_frozen(decorator):
    if not isinstance(decorator, ast.Call):
        return False
    for keyword in decorator.keywords:
        if keyword.arg == "frozen" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value is True
    return False


if __name__ == "__main__":
    unittest.main()
