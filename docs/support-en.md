# Offline Voice Studio — Support

[简体中文](support.md) · 2.5 build253 store candidate

Getting started
1. Open the application. The bundled local models and 35 confirmed voices are shared by all local profiles; do not reimport models that are already ready.
2. Type a draft or import TXT, select a voice, then set speed and paragraph pauses.
3. Generate speech, listen to it, and export WAV. A successful technical check is not a listening-quality approval. Check each new script's pronunciation, naturalness and timing yourself.

Local profiles
Choose Local Profiles in the sidebar. Create a name, open a saved profile, rename it, or return to the default profile. Each profile keeps its own draft, recent-file list, task settings and internally saved project/recording files. The last profile is restored after reopening the application. No email, password, cloud account or purchase activation is involved. Profiles do not restrict file access within the same macOS account.
The default profile retains existing data in its original location. External files stay where you saved them. Models and the built-in confirmed voice library are shared. Wait for a running task to finish, and resolve any save failure before switching profiles.

Chinese and English interface
Use Language / 界面语言 to choose System, 简体中文 or English. This changes application interface text only; it does not translate drafts, rename your roles or alter audio. Current input is saved before switching. The language selection applies across profiles. Native system dialogs and third-party diagnostic messages may follow the macOS language or retain their original wording.

Projects and local re-recording
Use a multi-role project to arrange clips, chapters, pronunciations and the waveform timeline. Chinese dialogue uses the established 00005 and 00367 workflow. Split a clip into sentences when you want to change one sentence. Re-record only the target clip, or use a permitted neighboring-sentence range when needed. Original audio is retained. Reliable positioning and consistency checks are required; the application will not guess unsafe cuts. A previously approved script does not establish the quality of a new script.

Saving and moving to another Mac
Save TXT for a draft, export WAV for finished audio, and use a complete project bundle to retain the project and its assets. Models can be included in a project bundle when that option is available; such bundles can be large. Keep a separate backup before important edits. The application cannot restore files that were moved or deleted outside its control. Old desktop data is not automatically bulk-migrated into the store sandbox: use the native open panel or restore a complete project bundle.

Requirements and limits
Apple silicon and macOS 26.2 or later. Full generation has been validated on a 32 GB Mac; a reliable lower-memory minimum has not been established. Ordinary drafts support up to 12000 Unicode scalar characters. A project speech clip supports up to 2000, with up to 12000 across the project. Clips with recognition differences and listening confirmations retain their existing review status.

Troubleshooting
If a task fails, preserve the original files and inspect the local Logs folder. A cancelled task may retain completed segments for resumption with the same script and settings. For playback trouble, check system audio output and reopen a known valid recording. A successful decode or hash check does not confirm how natural speech sounds.

Public support
https://github.com/spinodehong-web/offline-voice-studio-open-source/issues
Reading the documentation requires no GitHub login; opening an issue may require your own GitHub account. Do not post private text, recordings, complete projects, passwords or verification codes. The application does not upload them automatically.

Local data and older desktop recordings

Store data normally lives at `~/Library/Containers/com.spinode.offline-voice-studio/Data/Library/Application Support/Offline Voice Studio/`. Earlier desktop data remains at `~/Library/Application Support/Offline Voice Studio/`. These are not bulk-merged automatically. Use Open audio to select an existing WAV and complete project bundles to restore projects with assets; do not move or remove the old data root to force migration. New profiles keep their internal work under `LocalProfiles/<profile-id>/` below the store data root. External files remain at the locations you chose. Back up drafts, project assets, originals, history, logs, recovery files and external exports before any cleanup; uninstalling the app is not a complete data deletion.

Continuous neighboring-sentence re-recording is limited to permitted ranges of up to 600 characters and requires consistent voice, language, speed, gain and chapter, with intact audio and reliable positioning. Do not bypass rejected safety checks.

For support, report the app build, macOS version, chip and memory, minimal steps, expected and actual results, and a non-private error message. Public GitHub issues require your own GitHub account. Do not attach private recordings or complete projects. There is no promised response time or unestablished telephone/live support service. See the [privacy policy](privacy-en.md).

This 2.5 candidate is not yet published on the App Store. The [public repository](https://github.com/spinodehong-web/offline-voice-studio-open-source) provides 2.3 source, without this store candidate's models or 35-voice bundle.
