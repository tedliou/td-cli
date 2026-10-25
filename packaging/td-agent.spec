from PyInstaller.utils.hooks import collect_all
import os

datas, binaries, hiddenimports = collect_all("td_cli")
root = os.path.abspath(os.path.join(SPECPATH, ".."))
a = Analysis([os.path.join(root, "src/td_cli/agent_tool.py")], pathex=[os.path.join(root, "src")], binaries=binaries, datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
# onedir like td: skip the onefile extraction on every start; the runtime has its own name so all
# three bundles can share one install directory.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="td-agent", console=True, contents_directory="td-agent-runtime")
coll = COLLECT(exe, a.binaries, a.datas, name="td-agent")
