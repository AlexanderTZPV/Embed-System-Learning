# 同花顺盯盘助手

项目源码在 [AI_Money/](AI_Money/)，Windows 项目压缩包为 [AI_Money-Windows.zip](AI_Money-Windows.zip)。

## 下载到本地

1. 打开仓库分支 `codex/ths-watch-desktop`。
2. 点击 `AI_Money-Windows.zip`，再点击 GitHub 文件页面的下载按钮。
3. 将压缩包内容解压到 `G:\AI_Money`，确认 `G:\AI_Money\main.py` 存在。
4. 安装 Windows x64 Python 3.11 或 3.12，双击 `setup.bat`（或 `安装.bat`），然后运行 `demo.bat`（或 `演示.bat`）。
5. 实盘运行 `run.bat`（或 `启动.bat`），先完成区域标定。详见 [使用说明](AI_Money/README.md)。

本项目为源码交付，不含预编译 exe；64项自动化检查通过，真实 Windows 同花顺截图、API及exe仍须本机验收。原有学习资料保留。

若旧版出现“endencies 不是内部或外部命令”，请重新下载整个压缩包并覆盖程序文件，使用新增加的英文入口 `setup.bat`。不要删除 `runtime` 中的个人数据。
