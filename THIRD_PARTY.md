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
| Chatterbox声纹编码器 | https://github.com/resemble-ai/chatterbox | MIT；仅旧库研究路径可用，固定目录配音不需要该权重 |

许可文本参考 `licenses/`。模型转换版本、独立Python发行版、安装依赖及其传递依赖仍须检查各自随附许可。不要把模型卡或代码许可当作参考录音的授权。

构建自己的二进制发行版时须附全部适用第三方声明；FFmpeg/libsndfile等对应源码、替换/重链接权利等义务按所分发版本及实际组合处理。本仓库没有重授权第三方模型或将私人录音加入MIT范围。
