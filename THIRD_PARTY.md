# 第三方组件

MIT仅覆盖本仓库的自有应用代码；第三方许可保持独立。

| 组件 | 上游 | 许可与用途 |
| --- | --- | --- |
| Qwen3-TTS | https://github.com/QwenLM/Qwen3-TTS | Apache-2.0；本地TTS，权重未随库分发 |
| MLX Audio | https://github.com/Blaizzy/mlx-audio | MIT；Apple Silicon推理，依赖未随库分发 |
| MLX | https://github.com/ml-explore/mlx | MIT；本地计算 |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | MIT；本地识别 |
| Whisper | https://github.com/openai/whisper | MIT；识别模型，权重未随库分发 |
| FFmpeg | https://ffmpeg.org/ | 按具体编译配置适用LGPL/GPL；本项目原配置禁用GPL/nonfree和网络协议；不在本仓库分发二进制 |
| libsndfile / SoundFile | https://github.com/libsndfile/libsndfile / https://github.com/bastibe/python-soundfile | 分别保留LGPL及BSD许可；动态音频IO |
| Chatterbox声纹编码器（旧队列的可选路径） | https://github.com/resemble-ai/chatterbox | MIT；只做声纹相似度筛查，不负责本项目配音；默认固定目录模式不调用，不随库分发其权重 |

许可文本参考 `licenses/`。模型转换版本、独立Python发行版、安装依赖及其传递依赖仍须检查各自随附许可。不要把模型卡或代码许可当作参考录音的授权。

## 为什么保留Chatterbox许可证

源码中仍有实际引用：[QualityCheck.embedding](src/audition_qa.py) 从 `mlx_audio.tts.models.chatterbox_turbo` 导入 `VoiceEncoder`，由 `QualityCheck.evidence` 调用；[旧队列run_queue](src/library_production.py) 再调用 `qa.evidence`。用途是两份试听之间的说话人相似度筛查，不是自然度验收，也不是Qwen配音。

默认发布目录包含 `approved-voices.json`，[worker](src/worker.py) 会在固定目录模式拒绝启动该旧队列；因此正常固定参考声音配音、界面预览和源码构建都不需要Chatterbox声纹权重。本仓库也没有该权重。保留许可证是为了交代仍保留的可选源码路径，不表示使用者必须额外安装或启用Chatterbox。此路径存在，所以本轮没有把它按“完全未使用”删除。

构建自己的二进制发行版时须附全部适用第三方声明；FFmpeg/libsndfile等对应源码、替换/重链接权利等义务按所分发版本及实际组合处理。本仓库没有重授权第三方模型或将私人录音加入MIT范围。
