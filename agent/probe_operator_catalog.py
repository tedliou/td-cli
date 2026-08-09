"""Generate the locked-build Operator catalog from a live TouchDesigner runtime.

This module is a release-development probe, not part of the Agent Component runtime.
Run it only in the disposable diagnostic project documented in
``tools/td_diagnostic_bridge/README.md``.
"""

import json
from pathlib import Path

SCHEMA_VERSION = 1
PROBE_REVISION = 1
LOCKED_TOUCHDESIGNER_BUILD = "2025.32050"
FAMILIES = ("COMP", "TOP", "CHOP", "POP", "DAT", "MAT", "SOP")

# These categories are deliberately conservative. A matching Operator may be safe while idle,
# but td-cli does not claim unconditional creation for types whose purpose depends on external
# state or executes user code. Exact names take precedence over fragments.
SIDE_EFFECT_EXACT = {
    "process": {
        "engineCOMP",
        "performCHOP",
        "performDAT",
        "windowCOMP",
    },
    "loader": {
        "alembicSOP",
        "alembicinPOP",
        "cplusplusCHOP",
        "cplusplusDAT",
        "cplusplusPOP",
        "cplusplusSOP",
        "cplusplusTOP",
        "fbxCOMP",
        "substanceTOP",
        "substanceselectTOP",
        "usdCOMP",
    },
    "script": {
        "chopexecuteDAT",
        "datexecuteDAT",
        "executeDAT",
        "opexecuteDAT",
        "panelexecuteDAT",
        "parameterexecuteDAT",
        "pargroupexecuteDAT",
        "scriptCHOP",
        "scriptDAT",
        "scriptSOP",
        "scriptTOP",
    },
}

SIDE_EFFECT_FRAGMENTS = {
    "network": (
        "abletonlink",
        "artnet",
        "etherdream",
        "ndi",
        "mqtt",
        "oscin",
        "oscout",
        "renderstream",
        "socket",
        "st2110",
        "syncin",
        "syncout",
        "tcpip",
        "touchin",
        "touchout",
        "tuio",
        "udpin",
        "udpout",
        "udtin",
        "udtout",
        "web",
    ),
    "device": (
        "audiodevice",
        "blacktrax",
        "bodytrack",
        "directdisplay",
        "directxin",
        "directxout",
        "dmx",
        "facetrack",
        "freedin",
        "freedout",
        "heliosdac",
        "hokuyo",
        "joystick",
        "keyboardin",
        "kinect",
        "laserdevice",
        "leapmotion",
        "leuzerod",
        "midi",
        "mosys",
        "mousein",
        "mouseout",
        "multitouch",
        "ncam",
        "oakdevice",
        "oculus",
        "openvr",
        "optitrack",
        "orbbec",
        "ouster",
        "pangolin",
        "pantilt",
        "realsense",
        "serial",
        "sick",
        "tablet",
        "videodevice",
        "vioso",
        "zed",
    ),
    "file": (
        "audiofile",
        "filein",
        "fileout",
        "folder",
        "mediafile",
        "moviefile",
        "photoshop",
        "pointfile",
    ),
    "loader": ("importselect",),
    "process": ("pipein", "pipeout", "sharedmem"),
}


def enumerate_operator_classes(td_module):
    """Return exact built-in OP type names/classes exposed by this TD runtime."""
    operator_classes = []
    for name in dir(td_module):
        operator_class = getattr(td_module, name, None)
        if not isinstance(operator_class, type):
            continue
        try:
            op_type = getattr(operator_class, "opType", None)
        except (AttributeError, ImportError):
            continue
        if isinstance(op_type, str) and op_type == name and op_type.endswith(FAMILIES):
            operator_classes.append((op_type, operator_class))
    return sorted(operator_classes)


def operator_family(op_type):
    for family in FAMILIES:
        if op_type.endswith(family):
            return family
    raise ValueError(f"not an Operator type: {op_type}")


def side_effect_class(op_type):
    lowered = op_type.lower()
    for category, exact_names in SIDE_EFFECT_EXACT.items():
        if op_type in exact_names:
            return category
    for category, fragments in SIDE_EFFECT_FRAGMENTS.items():
        if any(fragment in lowered for fragment in fragments):
            return category
    return "pure"


def _unprobed_entry(op_type, side_effect, notes):
    return {
        "op_type": op_type,
        "family": operator_family(op_type),
        "status": "unsupported",
        "supported_on_os": None,
        "inputs": None,
        "outputs": None,
        "builtin_parameters": None,
        "custom_parameters": None,
        "side_effect_class": side_effect,
        "experimental": False,
        "create_verified": False,
        "notes": notes,
    }


def probe_operator_classes(operator_classes, container):
    """Create and immediately destroy every supplied Operator class."""
    entries = []
    for index, (op_type, operator_class) in enumerate(operator_classes):
        effect = side_effect_class(op_type)
        try:
            node = container.create(operator_class, f"probe_{index:04d}")
        except Exception as error:  # noqa: BLE001 - TD exposes per-OP exception types dynamically.
            entries.append(
                _unprobed_entry(
                    op_type,
                    effect,
                    [f"create failed: {type(error).__name__}: {error}"],
                )
            )
            continue

        try:
            actual_type = str(node.OPType)
            actual_family = str(node.family)
            exact = actual_type == op_type and actual_family == operator_family(op_type)
            supported = bool(node.supported)
            notes = [] if exact else [f"created {actual_type} instead of {op_type}"]
            if not supported:
                notes.append("OP.supported is false on this OS")
            if not exact or not supported:
                status = "unsupported"
            elif effect != "pure":
                status = "conditional"
            else:
                status = "supported"
            entries.append(
                {
                    "op_type": op_type,
                    "family": operator_family(op_type),
                    "status": status,
                    "supported_on_os": supported,
                    "inputs": len(node.inputConnectors),
                    "outputs": len(node.outputConnectors),
                    "builtin_parameters": len(node.builtinPars),
                    "custom_parameters": len(node.customPars),
                    "side_effect_class": effect,
                    "experimental": False,
                    "create_verified": exact,
                    "notes": notes,
                }
            )
        finally:
            node.destroy()
    return sorted(entries, key=lambda entry: entry["op_type"])


def build_manifest(td_module, container, touchdesigner_build):
    if str(touchdesigner_build) != LOCKED_TOUCHDESIGNER_BUILD:
        raise RuntimeError(
            f"TouchDesigner {LOCKED_TOUCHDESIGNER_BUILD} required; got {touchdesigner_build}"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "probe_revision": PROBE_REVISION,
        "touchdesigner_build": str(touchdesigner_build),
        "families": list(FAMILIES),
        "operators": probe_operator_classes(enumerate_operator_classes(td_module), container),
    }


def serialize_manifest(manifest):
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def run_probe(td_module, project_component, touchdesigner_build, output_path):
    """Run the full disposable probe and atomically replace the candidate manifest."""
    probe_root = project_component.create(td_module.baseCOMP, "__td_cli_operator_probe")
    try:
        manifest = build_manifest(td_module, probe_root, touchdesigner_build)
    finally:
        probe_root.destroy()
    output = Path(output_path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(serialize_manifest(manifest), encoding="utf-8")
    temporary.replace(output)
    return manifest
