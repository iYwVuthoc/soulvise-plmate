"""允许通过 ``python -m desktop_companion_agent`` 启动程序。"""

from desktop_companion_agent.app import main

if __name__ == "__main__":
    raise SystemExit(main())
