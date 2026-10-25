"""LANDrop-lite — 局域网文件传输

四个模式：
  share    把文件/目录共享给局域网（浏览器下载，支持断点续传）
  receive  开一个上传页面，局域网设备用浏览器把文件传到本机（分块上传，上限 10GB）
  text     把一段文本发布到局域网（手机打开即看、一键复制）
  discover 发现局域网里的 LANDrop 设备（mDNS/DNS-SD，零依赖自实现）

纯 Python 标准库，零依赖；可选安装 qrcode 以在终端显示二维码。
"""

__version__ = "0.4.0"
