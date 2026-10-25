"""localcore：工作区共享件（各本地工具复用的基础设施）。

导入约定：项目代码在自己包入口把工作区根加入 sys.path 后即可
`from localcore.net import fetch, probe, path_allowed`。
参见 ToolHub-工具箱门户/toolhub/__init__.py 的示范。
"""

__version__ = "0.1"
