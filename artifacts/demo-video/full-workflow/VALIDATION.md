# 成片验证

- 视频：`FurniScope-全功能操作演示.mp4`
- 时长：749.24 秒（12 分 29 秒）
- 画面：1920×1080，16:9，H.264 High，yuv420p，25 fps
- 大小：约 32.1 MiB；无音轨
- 嵌入章节：49；独立片段：49
- SHA-256：`a549efb74f4cd641c234565330f67081fea8ccd5819bbc814bbaa8c9ae84073f`
- 本次修订：按用户要求排除全部 5 个 AI 员工页面操作片段。

## 已完成的检查

1. FFmpeg 对成片完整解码，退出码 0。
2. 从修订后视频中逐章重新抽帧，共 49 帧；复核删除处的拼接及管理员登录首帧。保留片段沿用上一版已完成的画面核对，首页与密码输入片段保持不变。
3. 成片重新计算 SHA-256，与生成阶段的 manifest 一致。分段视频校验值见 `SHA256SUMS.txt`。
4. 两份实际 PDF 导出文件均有完整 PDF 文件头/结束标记，均为 6 页。校验值见 `verification/samples.json`。
5. 录制浏览器没有捕获到未处理的 pageerror。业务接口拒绝、功能开关和网络失败另列在 `COVERAGE.md`，不由此判为全部功能成功。
6. 确认当前成片清单、嵌入章节和 `chapters/` 均不包含 AI 员工操作片段。
7. 原录制完成时，前端 HTTP 200；API `/health/ready` 返回 ready，database/job_queue 为 ok。本次仅剪辑视频。

抽帧索引和 PDF 校验记录：`verification/samples.json`。

抽帧联系表：`verification/sheet-01.png` 至 `sheet-06.png`，每张按从左到右、从上到下排列，与章节索引顺序一致。

复核脚本：`scripts/verify_full_demo.py`。功能实际覆盖范围、演示数据写入和未执行动作见 `COVERAGE.md`。
