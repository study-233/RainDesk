# RainDesk · 雨课堂助手

RainDesk 是一款 Windows 桌面应用。它在后台监听雨课堂发题，调用 AI 生成答案与解析。对自建或获授权的测试班级，学生只需授权一次，后续场次即可自动签到、限时提交，并回读核对服务器收到的答案。

## 安装与使用

1. 从 [GitHub Releases](https://github.com/study-233/RainDesk/releases/latest) 下载 Windows x64 安装包，以管理员权限安装。支持 Windows 10/11，需要 Edge WebView2 Runtime。
2. 在「账号与 AI 设置」用学生微信扫码，选择 DeepSeek、Qwen、OpenAI、LM Studio 或自定义兼容接口，填写模型名称及所需密钥。
3. 在「课堂自动化与提醒」读取已加入的班级，选择测试班级并点击「记住此测试课程」。关闭管理窗口后，Windows 服务仍会监听课堂。可在概览页暂停本场提交，或在设置中撤销长期授权。

答题记录显示模型耗时、解析和提交回执。「已验证」表示服务器回读的答案与发送值一致。开课、发题和作答结果可通过 Windows 桌面提醒查看；已获取的课件可导出为 PDF 或图片型 PPTX。

## 从源码构建

桌面端使用 Tauri 2、React 和 TypeScript，后台使用 Python、FastAPI 和 SQLite。会话与模型密钥通过 Windows DPAPI 加密保存。在 Windows x64 上准备 Python 3.11、Node.js 22.12+、Rust 和 Visual Studio C++ 构建工具后，运行：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm.cmd ci
npm.cmd run package
```

安装包输出到 `release/`。运行 `.venv\Scripts\python.exe -m unittest discover -s tests -v` 可验证本地模拟课堂流程。协议参考与许可见 [第三方声明](THIRD-PARTY.txt)。
