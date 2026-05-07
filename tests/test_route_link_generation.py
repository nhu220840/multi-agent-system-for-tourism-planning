import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ITINERARY_BUILDER = ROOT / "app" / "agents" / "itinerary_builder.py"
RAG_SERVICE = ROOT / "app" / "services" / "rag_service.py"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function {name} not found")


def _dict_value_for_key(node: ast.Dict, key_name: str) -> ast.AST:
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == key_name:
            return value
    raise AssertionError(f"Key {key_name} not found")


class RouteLinkGenerationTests(unittest.TestCase):
    def test_itinerary_builder_route_leg_uses_shared_segment_url(self) -> None:
        module = _parse(ITINERARY_BUILDER)
        self.assertFalse(
            any(isinstance(node, ast.FunctionDef) and node.name == "_quick_segment_map_url" for node in module.body)
        )

        fn = _find_function(module, "_build_route_leg")
        payload_dict = None
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "payload"
                and isinstance(node.value, ast.Dict)
            ):
                payload_dict = node.value
                break
        self.assertIsNotNone(payload_dict, "payload assignment not found")
        segment_expr = _dict_value_for_key(payload_dict, "segment_map_url")
        self.assertIsInstance(segment_expr, ast.Call)
        self.assertIsInstance(segment_expr.func, ast.Name)
        self.assertEqual("_segment_map_url", segment_expr.func.id)

    def test_rag_service_route_plan_uses_shared_segment_url(self) -> None:
        module = _parse(RAG_SERVICE)
        self.assertFalse(
            any(isinstance(node, ast.FunctionDef) and node.name == "_quick_segment_map_url" for node in module.body)
        )

        fn = _find_function(module, "_build_route_plan")
        route_dicts = [
            node for node in ast.walk(fn)
            if isinstance(node, ast.Dict)
            and any(isinstance(key, ast.Constant) and key.value == "segment_map_url" for key in node.keys)
        ]
        self.assertTrue(route_dicts, "route payload dict not found")
        segment_expr = _dict_value_for_key(route_dicts[0], "segment_map_url")
        self.assertIsInstance(segment_expr, ast.Call)
        self.assertIsInstance(segment_expr.func, ast.Name)
        self.assertEqual("segment_map_url", segment_expr.func.id)


if __name__ == "__main__":
    unittest.main()
