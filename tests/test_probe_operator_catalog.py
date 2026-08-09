import json
import runpy
from pathlib import Path
from types import SimpleNamespace

catalog = SimpleNamespace(**runpy.run_path("agent/probe_operator_catalog.py"))


class FakeNode:
    def __init__(
        self,
        op_type: str,
        family: str,
        *,
        supported: bool = True,
        inputs: int = 1,
        outputs: int = 1,
    ) -> None:
        self.OPType = op_type
        self.family = family
        self.supported = supported
        self.inputConnectors = [object()] * inputs
        self.outputConnectors = [object()] * outputs
        self.builtinPars = [object(), object()]
        self.customPars = [object()]
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class FakeContainer:
    def __init__(self, outcomes: dict[str, FakeNode | Exception]) -> None:
        self.outcomes = outcomes
        self.created_nodes: list[FakeNode] = []

    def create(self, operator_class, _name: str):
        outcome = self.outcomes[operator_class.opType]
        if isinstance(outcome, Exception):
            raise outcome
        self.created_nodes.append(outcome)
        return outcome


def operator_class(op_type: str):
    return type(op_type, (), {"opType": op_type})


class ImportingMeta(type):
    def __getattr__(cls, name: str):
        raise ModuleNotFoundError(name)


def test_enumerate_operator_classes_uses_exact_td_optype_contract() -> None:
    noise = operator_class("noiseTOP")
    namespace = SimpleNamespace(
        noiseTOP=noise,
        alias=operator_class("noiseTOP"),
        TOP=type("TOP", (), {"opType": None}),
        importing_type=ImportingMeta("importing_type", (), {}),
        helper=object(),
    )

    assert catalog.enumerate_operator_classes(namespace) == [("noiseTOP", noise)]


def test_probe_records_verified_shape_and_conservative_status() -> None:
    noise_class = operator_class("noiseTOP")
    video_class = operator_class("videodeviceinTOP")
    failed_class = operator_class("brokenSOP")
    noise = FakeNode("noiseTOP", "TOP", inputs=2)
    video = FakeNode("videodeviceinTOP", "TOP")
    container = FakeContainer(
        {
            "noiseTOP": noise,
            "videodeviceinTOP": video,
            "brokenSOP": RuntimeError("cannot create"),
        }
    )

    entries = catalog.probe_operator_classes(
        [("noiseTOP", noise_class), ("videodeviceinTOP", video_class), ("brokenSOP", failed_class)],
        container,
        experimental_build=False,
    )

    assert entries[0] == {
        "op_type": "brokenSOP",
        "family": "SOP",
        "status": "unsupported",
        "supported_on_os": None,
        "inputs": None,
        "outputs": None,
        "builtin_parameters": None,
        "custom_parameters": None,
        "side_effect_class": "pure",
        "experimental": False,
        "create_verified": False,
        "notes": ["create failed: RuntimeError: cannot create"],
    }
    assert entries[1]["op_type"] == "noiseTOP"
    assert entries[1]["status"] == "supported"
    assert entries[1]["inputs"] == 2
    assert entries[2]["side_effect_class"] == "device"
    assert entries[2]["status"] == "conditional"
    assert noise.destroyed is True
    assert video.destroyed is True


def test_probe_rejects_inexact_create_result() -> None:
    requested = operator_class("noiseTOP")
    inexact = FakeNode("nullTOP", "TOP")
    container = FakeContainer({"noiseTOP": inexact})

    entry = catalog.probe_operator_classes(
        [("noiseTOP", requested)], container, experimental_build=False
    )[0]

    assert entry["status"] == "unsupported"
    assert entry["create_verified"] is False
    assert entry["notes"] == ["created nullTOP instead of noiseTOP"]
    assert inexact.destroyed is True


def test_build_manifest_is_sorted_and_serializes_deterministically() -> None:
    namespace = SimpleNamespace(
        nullTOP=operator_class("nullTOP"),
        baseCOMP=operator_class("baseCOMP"),
    )
    container = FakeContainer(
        {
            "nullTOP": FakeNode("nullTOP", "TOP"),
            "baseCOMP": FakeNode("baseCOMP", "COMP", inputs=0, outputs=0),
        }
    )

    manifest = catalog.build_manifest(namespace, container, "2025.32050", experimental_build=False)
    encoded = catalog.serialize_manifest(manifest)

    assert manifest["schema_version"] == 1
    assert manifest["probe_revision"] == 2
    assert manifest["touchdesigner_build"] == "2025.32050"
    assert manifest["experimental_build"] is False
    assert manifest["families"] == ["COMP", "TOP", "CHOP", "POP", "DAT", "MAT", "SOP"]
    assert [entry["op_type"] for entry in manifest["operators"]] == ["baseCOMP", "nullTOP"]
    assert encoded.endswith("\n")
    assert json.loads(encoded) == manifest
    assert encoded == catalog.serialize_manifest(manifest)


def test_locked_build_candidate_manifest_has_complete_schema_and_seven_families() -> None:
    manifest = json.loads(
        Path("agent/touchdesigner-2025.32050-operators.json").read_text(encoding="utf-8")
    )
    schema = json.loads(Path("agent/operator_catalog.schema.json").read_text(encoding="utf-8"))
    required = set(schema["$defs"]["operator"]["required"])
    allowed_statuses = {"supported", "conditional", "unsupported", "unknown"}
    allowed_effects = {"pure", "device", "file", "network", "process", "loader", "script"}
    operators = manifest["operators"]

    assert manifest["touchdesigner_build"] == "2025.32050"
    assert manifest["experimental_build"] is False
    assert manifest["families"] == ["COMP", "TOP", "CHOP", "POP", "DAT", "MAT", "SOP"]
    assert len(operators) == 680
    assert [entry["op_type"] for entry in operators] == sorted(
        entry["op_type"] for entry in operators
    )
    assert {entry["family"] for entry in operators} == set(manifest["families"])
    assert all(set(entry) == required for entry in operators)
    assert all(entry["status"] in allowed_statuses for entry in operators)
    assert all(entry["side_effect_class"] in allowed_effects for entry in operators)
    assert all(
        entry["status"] != "supported" or entry["side_effect_class"] == "pure"
        for entry in operators
    )
    assert all(entry["experimental"] is False for entry in operators)
