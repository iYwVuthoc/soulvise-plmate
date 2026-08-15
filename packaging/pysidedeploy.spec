[app]
title = SoulvisePlmate
project_dir = .
input_file = SoulvisePlmate.py
exec_directory = work/deploy-output
project_file =
icon = src/desktop_companion_agent/resources/app_icon.ico

[python]
python_path = .venv\Scripts\python.exe
packages = Nuitka==4.1.3,zstandard==0.25.0
android_packages = buildozer==1.5.0,cython==0.29.33

[qt]
qml_files =
excluded_qml_plugins =
modules = Core,Gui,Widgets
plugins = platforminputcontexts,imageformats,iconengines

[android]
wheel_pyside =
wheel_shiboken =
plugins =

[nuitka]
macos.permissions =
mode = onefile
extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --windows-console-mode=disable --user-plugin=packaging/nuitka_codex_bytecode.py --include-package=keyring.backends --include-package=openai_codex --include-package=codex_cli_bin --include-package=acp --include-raw-dir=.venv/Lib/site-packages/codex_cli_bin/bin=codex_cli_bin/bin --include-raw-dir=.venv/Lib/site-packages/codex_cli_bin/codex-path=codex_cli_bin/codex-path --include-raw-dir=.venv/Lib/site-packages/codex_cli_bin/codex-resources=codex_cli_bin/codex-resources --include-data-file=.venv/Lib/site-packages/codex_cli_bin/codex-package.json=codex_cli_bin/codex-package.json --include-data-dir=src/desktop_companion_agent/resources=desktop_companion_agent/resources --company-name=Soulvise --product-name="Soulvise Plmate" --file-version=0.2.0.0 --product-version=0.2.0.0 --file-description="Soulvise Plmate" --copyright="Copyright (c) 2026 Soulvise"

[buildozer]
mode = debug
recipe_dir =
jars_dir =
ndk_path =
sdk_path =
local_libs =
arch =
