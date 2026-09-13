"""Run in an isolated Execute DAT; does not load Agent or connect a Daemon."""

import hashlib
import os
import runpy
import traceback
from pathlib import Path


def probe(td_op, constant_type, build):
    source = Path(__file__).resolve().parents[1] / "agent" / "extension.py"
    module = runpy.run_path(str(source))
    control = module["OperatorControl"](td_op, None)
    node = td_op("/project1").create(constant_type, "constant_sequence_probe")
    result = {
        "pid": os.getpid(),
        "build": str(build),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    try:
        read = {
            "name": "parameters.sequence.get",
            "input": {"operator_path": node.path, "sequence": "const", "max_parameters": 2},
        }
        initial = control.execute(read)
        assert [p["parameter"] for p in initial["blocks"][0]["parameters"]] == ["name", "value"]
        blocks = [
            {
                "name": None,
                "parameters": [
                    {"parameter": "name", "mode": "constant", "value": f"channel{i}"},
                    {
                        "parameter": "value",
                        "mode": "expression" if i == 1 else "constant",
                        "value": "1 + 2" if i == 1 else float(i),
                    },
                ],
            }
            for i in range(3)
        ]
        replaced = control.execute(
            {
                "name": "parameters.sequence.replace",
                "input": {"operator_path": node.path, "sequence": "const", "blocks": blocks},
            }
        )
        assert replaced["blocks"] == blocks
        assert control.execute(read)["blocks"] == blocks
        assert node.par.const1value.eval() == 3.0
        malformed = [{"name": None, "parameters": blocks[0]["parameters"][:1]}]
        try:
            control.execute(
                {
                    "name": "parameters.sequence.replace",
                    "input": {"operator_path": node.path, "sequence": "const", "blocks": malformed},
                }
            )
        except module["AgentCommandError"] as error:
            assert error.code == "parameter_sequence_shape_invalid"
        else:
            raise AssertionError("malformed replacement was accepted")
        assert control.execute(read)["blocks"] == blocks
        result.update(
            validated=True,
            initial=initial,
            replaced=replaced,
            malformed_preserved=True,
            evaluated_expression=3.0,
        )
    except Exception:  # noqa: BLE001 - persist any locked-runtime probe failure
        result.update(validated=False, error=traceback.format_exc())
    finally:
        node.destroy()
    return result
