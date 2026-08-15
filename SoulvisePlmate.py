"""Windows部署入口，文件名决定最终可执行程序名称。"""

from desktop_companion_agent.app import main

if __name__ == "__main__":
    raise SystemExit(main())
