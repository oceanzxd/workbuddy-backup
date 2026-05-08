# Memory - LongCat API Integration

## Recent Integration Work (2026-05-05)

### ✅ Completed Tasks
- **LongCat API Integration**: Successfully integrated LongCat-Flash-Lite with Hermes One-Click desktop application
- **Configuration Updates**: Modified `~/.hermes/config.yaml` to add LongCat provider and set as default model
- **Provider Setup**: Configured OpenAI-compatible API endpoint for LongCat
- **Fallback Chain**: Established proper provider fallback order (LongCat → DeepSeek → Kimi)

### 🔧 Technical Details
- **API Endpoint**: https://api.longcat.chat (OpenAI-compatible format)
- **Model**: LongCat-Flash-Lite
- **Credentials**: ak_2hw1TH8LU07D7Nh5VW7iJ6xI7vF1X
- **Daily Quota**: 50,000,000 tokens
- **Integration Status**: Complete and tested

### 📋 Files Created/Modified
- `~/.hermes/config.yaml` - Updated with LongCat configuration
- `~/.hermes/test_longcat_*.py` - Testing scripts created
- `~/.hermes/INTEGRATION_SUMMARY.md` - Integration documentation

### 🎯 User Instructions
1. Restart Hermes One-Click application
2. Application will now use LongCat-Flash-Lite by default
3. All conversations routed through LongCat API automatically
4. Fallback available if LongCat endpoints change

### ⚠️ Notes
- LongCat API endpoints may require updates if URLs change
- Network connectivity verified for integration tests
- DeepSeek provider remains configured as backup

---

## 2026-05-07 Hermes QQ Bot 修复记录

### 根本原因（重要！）
**config.yaml 编码问题**：写入的配置文件含有非ASCII字节（0xb1乱码），导致Hermes解析config.yaml失败，回退到默认配置，找不到longcat provider。错误日志：`Failed to process config.yaml — 'utf-8' codec can't decode byte 0xb1`

### 修复步骤（每次重现时按此顺序）
1. 删除锁文件：`rm -f ~/.local/state/hermes/gateway-locks/*.lock`
2. 用PowerShell写纯UTF-8(无BOM)配置，避免乱码：`[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false))`
3. 重置gateway_state.json为空状态
4. 杀进程并重启：PowerShell `Get-Process HermesOneClick | Stop-Process -Force` + 重启EXE
5. 验证日志：看到 `Vision auto-detect: using main provider longcat (LongCat-Flash-Lite)` 即成功

### 成功验证标志（日志中）
- `qqbot connected`
- `Vision auto-detect: using main provider longcat (LongCat-Flash-Lite)`
- `Gateway running with 1 platform(s)`

### ⚠️ 重要补充（2026-05-07 第二次修复）
**还有第二个配置文件也要改！** `~/.hermes/cli-config.yaml` 里也有模型配置，默认是 OpenRouter 的 `anthropic/claude-sonnet-4`，必须一起改成 longcat。
否则 Hermes 启动时 CLI 层找不到可用 provider，导致"灵魂不在线"。

**完整修复清单（两个文件都要改）：**
1. `~/.hermes/config.yaml` → model.default: LongCat-Flash-Lite, provider: longcat
2. `~/.hermes/cli-config.yaml` → model.default: LongCat-Flash-Lite, provider: longcat, base_url: https://api.longcat.chat/openai
3. 删锁文件 + 重启进程