"""Executed by TouchDesigner 2025.32050 to assemble and save td-agent.tox."""

# TouchDesigner injects the `op`, `baseCOMP`, and `args` globals.
source_dir, output_path = args  # type: ignore[name-defined]
agent = op("/project1").create(baseCOMP, "td_agent")  # type: ignore[name-defined]
agent.par.externaltox = ""
agent.save(output_path)
