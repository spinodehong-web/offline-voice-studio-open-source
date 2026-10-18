import Cocoa
import CryptoKit
import AVFoundation
import UniformTypeIdentifiers
import Darwin

struct NarrationRequestParameters {
    static let speeds=[1.0,0.9,1.1,1.2]
    static let gaps=[0.3,0.6,1.0,0.0,2.0]
    let draft:String;let voice:String;let speedIndex:Int;let gapIndex:Int
    init?(_ request:[String:Any]) {
        guard let draft=request["text"] as? String,!draft.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,draft.unicodeScalars.count<=12000,!draft.contains("\0"),let voice=request["voice_id"] as? String,!voice.isEmpty,
              let speed=request["speed"] as? NSNumber,CFGetTypeID(speed) != CFBooleanGetTypeID(),
              let gap=request["paragraph_gap"] as? NSNumber,CFGetTypeID(gap) != CFBooleanGetTypeID(),
              let si=Self.speeds.firstIndex(of:speed.doubleValue),let gi=Self.gaps.firstIndex(of:gap.doubleValue) else{return nil}
        self.draft=draft;self.voice=voice;self.speedIndex=si;self.gapIndex=gi
    }
}

@main
final class VoiceStudio: NSObject, NSApplicationDelegate, NSTextViewDelegate, AVAudioPlayerDelegate {
    var projectEditor:ProjectEditor?
    var studioProjectHandler:(([String:Any])->Void)?
    var projectBusyChanged:(()->Void)?
    var projectTaskFinished:(()->Void)?
    var window: NSWindow!
    var text: NSTextView!
    var status: NSTextField!
    var characterCount:NSTextField!
    var validDraft:Bool { !text.string.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty && !text.string.contains("\0") && text.string.unicodeScalars.count<=12000 }
    var generateButton: NSButton!
    var importButton: NSButton!
    var cancelButton: NSButton!
    var playButton: NSButton!
    var exportButton: NSButton!
    var resumeButton: NSButton!
    var revealSavedButton: NSButton!
    var lastSavedURL: URL?
    var spinner: NSProgressIndicator!
    var process: Process?
    var player: AVAudioPlayer?
    var pauseButton:NSButton!
    var stopButton:NSButton!
    var playbackSlider:NSSlider!
    var playbackTime:NSTextField!
    var playbackTimer:Timer?
    var loadedAudioDuration:TimeInterval=0
    var pendingPlaybackTime:TimeInterval=0
    var playbackFinished=false
    var audioURL: URL?
    var voicePicker = NSPopUpButton()
    var speedPicker = NSPopUpButton()
    var gapPicker = NSPopUpButton()
    var voiceIDs = ["natural-female","warm-male","deep-male","clear-female"]
    var lastControlVoice:String?
    var lastControlSpeed:Int?
    var lastControlGap:Int?
    var idleActionButtons=[NSButton]()
    var pageOffset=0
    var matchTotal=0
    var searchField = NSSearchField()
    var statePicker = NSPopUpButton()
    var librarySummary: NSTextField!
    var afterRun: (() -> Void)?
    var lastError: String?
    var mediaResultRejected=false
    var workerReportedError=false
    var expectedWorkerEvent:String?
    var workerResultReceived=false
    var lastAudition: [String:Any]?
    var queueButton: NSButton!
    var auditionButton: NSButton!
    var reviewButton: NSButton!
    var voicedSelection = ""
    var approvedOnly: Bool { FileManager.default.fileExists(atPath: resources.appendingPathComponent("Engine/approved-voices.json").path) }
    var approvedCount:Int { ((try? JSONSerialization.jsonObject(with:Data(contentsOf:resources.appendingPathComponent("Engine/approved-voices.json")))) as? [String:Any])?["voices"].flatMap { $0 as? [[String:Any]] }?.count ?? 0 }
    var ready = false
    var importing: [URL] = []
    var cancelledByUser=false
    var buffer = Data()
    let resources = Bundle.main.resourceURL!
    var data: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Offline Voice Studio Open Source", isDirectory:true) }

    static func main() {
        let app = NSApplication.shared; let delegate = VoiceStudio()
        app.delegate = delegate; app.setActivationPolicy(.regular); app.run()
        withExtendedLifetime(delegate) {}
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu=NSMenu(); let appMenu=NSMenuItem();menu.addItem(appMenu);let sub=NSMenu();sub.addItem(withTitle:"关于离线配音工作室",action:#selector(showAbout),keyEquivalent:"");sub.addItem(.separator());sub.addItem(withTitle:"退出",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q");appMenu.submenu=sub
        let editItem=NSMenuItem();menu.addItem(editItem);let edit=NSMenu(title:"编辑");edit.addItem(withTitle:"撤销",action:Selector(("undo:")),keyEquivalent:"z");edit.addItem(withTitle:"重做",action:Selector(("redo:")),keyEquivalent:"Z");edit.addItem(NSMenuItem.separator());edit.addItem(withTitle:"剪切",action:#selector(NSText.cut(_:)),keyEquivalent:"x");edit.addItem(withTitle:"复制",action:#selector(NSText.copy(_:)),keyEquivalent:"c");edit.addItem(withTitle:"粘贴",action:#selector(NSText.paste(_:)),keyEquivalent:"v");edit.addItem(withTitle:"全选",action:#selector(NSText.selectAll(_:)),keyEquivalent:"a");edit.addItem(.separator());edit.addItem(withTitle:"查找与替换…",action:#selector(findReplaceDraft),keyEquivalent:"f");editItem.submenu=edit;NSApp.mainMenu=menu
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:1100,height:940),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false);window.title="离线配音工作室";window.minSize=NSSize(width:1000,height:900);window.center();window.isReleasedWhenClosed=false
        let root=NSStackView();root.orientation = .vertical;root.alignment = .leading;root.spacing=12;root.translatesAutoresizingMaskIntoConstraints=false;window.contentView!.addSubview(root)
        NSLayoutConstraint.activate([root.leadingAnchor.constraint(equalTo:window.contentView!.leadingAnchor,constant:32),root.trailingAnchor.constraint(equalTo:window.contentView!.trailingAnchor,constant:-32),root.topAnchor.constraint(equalTo:window.contentView!.topAnchor,constant:28),root.bottomAnchor.constraint(equalTo:window.contentView!.bottomAnchor,constant:-28)])
        let title=NSTextField(labelWithString:"把文字，讲得自然一些。");title.font = .systemFont(ofSize:28,weight:.semibold);root.addArrangedSubview(title)
        let intro=NSTextField(wrappingLabelWithString:"原创声音库 · 中英文配音\n中文或英文文稿在本机生成，无需账号、密钥或付费服务。基础配音使用原两个模型包；新增声音需另导入 VoiceModel-Identity.zip。");if approvedOnly {intro.stringValue="\(approvedCount) 款用户已确认音色 · 本地中英文配音\n直接输入中文或英文，自动选择语言。中文听感待确认；长文逐段保存，可恢复原任务。"};intro.font = .systemFont(ofSize:14);intro.textColor = .secondaryLabelColor;root.addArrangedSubview(intro)
        let searchRow=NSStackView();searchRow.orientation = .horizontal;searchRow.spacing=10
        searchField.placeholderString="搜索声音：男声、女声、口音、年龄、质感或编号";searchField.target=self;searchField.action=#selector(searchVoices);searchField.widthAnchor.constraint(equalToConstant:420).isActive=true
        statePicker.addItems(withTitles:["全部设计","已生成待试听","试听已确认","相似待复核","检查失败"]);statePicker.target=self;statePicker.action=#selector(searchVoices)
        searchRow.addArrangedSubview(searchField);if !approvedOnly { searchRow.addArrangedSubview(statePicker) };searchRow.addArrangedSubview(button("搜索 / 刷新",#selector(searchVoices)));searchRow.addArrangedSubview(button("上一页",#selector(previousVoices)));searchRow.addArrangedSubview(button("下一页",#selector(nextVoices)));root.addArrangedSubview(searchRow)
        librarySummary=NSTextField(wrappingLabelWithString:"设计数量不代表已经生成、独特或通过验收的声音数量。");librarySummary.font = .systemFont(ofSize:12);librarySummary.textColor = .secondaryLabelColor;root.addArrangedSubview(librarySummary)
        let options=NSStackView();options.orientation = .horizontal;options.spacing=10
        if approvedOnly {voiceIDs=[];voicePicker.removeAllItems()}
        else {voicePicker.addItems(withTitles:["自然女声 · 原有配置","温暖男声 · 设计","沉稳男声 · 设计","明亮女声 · 设计"])}
        voicePicker.target=self;voicePicker.action=#selector(voiceChanged);voicePicker.widthAnchor.constraint(lessThanOrEqualToConstant:620).isActive=true
        speedPicker.addItems(withTitles:["语速 1.0×","语速 0.9×","语速 1.1×","语速 1.2×"])
        gapPicker.addItems(withTitles:["段落停顿 0.3秒","段落停顿 0.6秒","段落停顿 1.0秒","无额外段落停顿","段落停顿 2.0秒"])
        speedPicker.selectItem(at:min(3,max(0,UserDefaults.standard.integer(forKey:"studioSpeedIndex"))));gapPicker.selectItem(at:min(4,max(0,UserDefaults.standard.integer(forKey:"studioGapIndex"))))
        lastControlSpeed=speedPicker.indexOfSelectedItem;lastControlGap=gapPicker.indexOfSelectedItem
        speedPicker.target=self;speedPicker.action=#selector(settingsChanged);gapPicker.target=self;gapPicker.action=#selector(settingsChanged)
        options.addArrangedSubview(voicePicker);options.addArrangedSubview(speedPicker);options.addArrangedSubview(gapPicker);root.addArrangedSubview(options)
        let tools=NSStackView();tools.orientation = .horizontal;tools.spacing=12;tools.addArrangedSubview(button("导入文稿 TXT…",#selector(importText)));tools.addArrangedSubview(button("保存文稿 TXT…",#selector(saveText)));tools.addArrangedSubview(button("声音试听样稿",#selector(sampleText)));tools.addArrangedSubview(button("打开已有录音…",#selector(openAudio)));tools.addArrangedSubview(button("最近录音列表…",#selector(chooseRecentAudio)));resumeButton=button("恢复上次任务",#selector(resumeLastNarration));if approvedOnly {tools.addArrangedSubview(resumeButton)};root.addArrangedSubview(tools)
        let libraryTools=NSStackView();libraryTools.orientation = .horizontal;libraryTools.spacing=10
        auditionButton=button("查看所选声音试听",#selector(loadAudition));libraryTools.addArrangedSubview(auditionButton)
        libraryTools.addArrangedSubview(button("制作所选声音两段试听",#selector(queueSelected)))
        queueButton=button("继续已保存试听队列",#selector(resumeQueue));libraryTools.addArrangedSubview(queueButton)
        libraryTools.addArrangedSubview(button("批量安排下一组 16 款",#selector(queueNext)))
        reviewButton=button("确认当前声音听感…",#selector(reviewVoice));reviewButton.isEnabled=false;libraryTools.addArrangedSubview(reviewButton);libraryTools.addArrangedSubview(button("当前项后暂停队列",#selector(pauseQueue)));if !approvedOnly { root.addArrangedSubview(libraryTools) }
        let scroll=NSScrollView();scroll.hasVerticalScroller=true;scroll.borderType = .bezelBorder;scroll.translatesAutoresizingMaskIntoConstraints=false
        text=NSTextView(frame:NSRect(x:0,y:0,width:800,height:260));text.delegate=self;text.isRichText=false;text.allowsUndo=true;text.isAutomaticQuoteSubstitutionEnabled=false;text.font = .systemFont(ofSize:17);text.textContainerInset=NSSize(width:12,height:12);text.autoresizingMask=[.width];text.string="Take a slow breath. The room was quiet, and for a moment, I thought we were finally safe.";if let draft=UserDefaults.standard.string(forKey:"studioNarrationDraft") {text.string=draft};scroll.documentView=text;root.addArrangedSubview(scroll);scroll.widthAnchor.constraint(equalTo:root.widthAnchor).isActive=true;scroll.heightAnchor.constraint(greaterThanOrEqualToConstant:230).isActive=true
        characterCount=NSTextField(labelWithString:"");characterCount.font = .systemFont(ofSize:12);root.addArrangedSubview(characterCount)
        let actions=NSStackView();actions.orientation = .horizontal;actions.spacing=12
        generateButton=button("生成配音",#selector(generate));generateButton.keyEquivalent="\r";actions.addArrangedSubview(generateButton)
        cancelButton=button("取消",#selector(cancel));cancelButton.isEnabled=false;actions.addArrangedSubview(cancelButton)
        importButton=button("导入模型包…",#selector(importModels));actions.addArrangedSubview(importButton)
        actions.addArrangedSubview(button("项目工作室…",#selector(openProjectStudio)));actions.addArrangedSubview(button("录音文件夹",#selector(openRecordings)));root.addArrangedSubview(actions)
        spinner=NSProgressIndicator();spinner.style = .bar;spinner.isIndeterminate=true;spinner.isHidden=true;root.addArrangedSubview(spinner);spinner.widthAnchor.constraint(equalTo:root.widthAnchor).isActive=true
        status=NSTextField(wrappingLabelWithString:"正在检查本地模型…");status.font = .systemFont(ofSize:14);root.addArrangedSubview(status)
        let result=NSStackView();result.orientation = .horizontal;result.spacing=12;result.addArrangedSubview(button("打开最近录音",#selector(openRecentAudio)));if approvedOnly {result.addArrangedSubview(button("试听已确认声音…",#selector(playApprovedAudition)))};playButton=button("试听",#selector(play));exportButton=button("导出 WAV…",#selector(exportAudio));playButton.isEnabled=false;exportButton.isEnabled=false;result.addArrangedSubview(playButton);result.addArrangedSubview(exportButton);pauseButton=button("暂停 / 继续",#selector(pauseAudio));stopButton=button("停止试听",#selector(stopAudio));pauseButton.isEnabled=false;stopButton.isEnabled=false;result.addArrangedSubview(pauseButton);result.addArrangedSubview(stopButton);revealSavedButton=button("在访达中显示",#selector(revealSavedFile));revealSavedButton.isEnabled=false;result.addArrangedSubview(revealSavedButton);root.addArrangedSubview(result)
        let playbackRow=NSStackView();playbackRow.orientation = .horizontal;playbackRow.spacing=12
        playbackSlider=NSSlider(value:0,minValue:0,maxValue:1,target:self,action:#selector(seekAudio));playbackSlider.isEnabled=false;playbackSlider.isContinuous=true;playbackSlider.widthAnchor.constraint(equalToConstant:700).isActive=true
        playbackTime=NSTextField(labelWithString:"0:00 / 0:00");playbackTime.font = .monospacedDigitSystemFont(ofSize:12,weight:.regular);playbackRow.addArrangedSubview(playbackSlider);playbackRow.addArrangedSubview(playbackTime);root.addArrangedSubview(playbackRow)
        playbackTimer=Timer.scheduledTimer(timeInterval:0.2,target:self,selector:#selector(refreshPlayback),userInfo:nil,repeats:true)
        let footer=NSTextField(wrappingLabelWithString:"录音独立保存，不覆盖旧文件。支持 Apple 芯片 Mac；长文稿需要数分钟。第三方许可见“关于”。");footer.font = .systemFont(ofSize:12);footer.textColor = .secondaryLabelColor;root.addArrangedSubview(footer)
        if let path=UserDefaults.standard.string(forKey:"studioLastSavedFile"),FileManager.default.fileExists(atPath:path) {lastSavedURL=URL(fileURLWithPath:path);revealSavedButton.isEnabled=true}
        try? FileManager.default.createDirectory(at:data,withIntermediateDirectories:true)
        updateDraftState();window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true);afterRun={ [weak self] in self?.searchVoices() };run(["--health"])
    }
    func textDidChange(_ notification:Notification) {UserDefaults.standard.set(text.string,forKey:"studioNarrationDraft");invalidateGeneratedAudio();updateDraftState();status.stringValue="文稿已修改，生成后可试听或导出。"}
    func invalidateGeneratedAudio() {status?.toolTip=nil;player?.stop();player=nil;audioURL=nil;playButton?.isEnabled=false;exportButton?.isEnabled=false;resetPlaybackSelection()}
    func button(_ title:String,_ action:Selector)->NSButton {
        let b=NSButton(title:title,target:self,action:action);b.bezelStyle = .rounded
        let idleActions:[Selector]=[#selector(searchVoices),#selector(previousVoices),#selector(nextVoices),#selector(importText),#selector(sampleText),#selector(openAudio),#selector(chooseRecentAudio),#selector(openRecentAudio),#selector(playApprovedAudition)]
        if idleActions.contains(action) {idleActionButtons.append(b)}
        return b
    }
    func setBusy(_ busy:Bool) {for b in idleActionButtons {b.isEnabled = !busy};searchField.isEnabled = !busy;statePicker.isEnabled = !busy;text.isEditable = !busy;resumeButton?.isEnabled = !busy && ready && UserDefaults.standard.string(forKey:"studioLastRequest") != nil;queueButton?.isEnabled = !busy && ready;auditionButton?.isEnabled = !busy;voicePicker.isEnabled = !busy;speedPicker.isEnabled = !busy;gapPicker.isEnabled = !busy;generateButton.isEnabled = !busy && ready && !voiceIDs.isEmpty && validDraft;importButton.isEnabled = !busy;cancelButton.isEnabled=busy;spinner.isHidden = !busy;if busy {spinner.startAnimation(nil)} else {spinner.stopAnimation(nil)};projectBusyChanged?()}
    func run(_ args:[String]) {
        guard process == nil else{return}
        let p=Process()
        p.executableURL=URL(fileURLWithPath:"/usr/bin/sandbox-exec");p.arguments=["-p","(version 1)(allow default)(deny network*)",resources.appendingPathComponent("Runtime/bin/python3").path,"-B","-I",resources.appendingPathComponent("Engine/worker.pyc").path]+args;p.currentDirectoryURL=data
        p.environment=["HOME":FileManager.default.homeDirectoryForCurrentUser.path,"PATH":"/usr/bin:/bin","LANG":"en_US.UTF-8","PYTHONUTF8":"1","OFFLINE_VOICE_DATA":data.path,"HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1"]
        var expected:String?
        switch args.first {
        case "--studio-project":expected="studio_project"
        case "--health":expected="health"
        case "--import-pack":expected="imported"
        case "--generate":expected="complete"
        case "--library-request":
            if args.count>1,let bytes=try? Data(contentsOf:URL(fileURLWithPath:args[1])),let body=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],(body["action"] as? String ?? "list")=="list" {expected="voices"}
        default:break
        }
        startWorkerProcess(p,expectedEvent:expected)
    }
    func startWorkerProcess(_ p:Process,logDirectory:URL? = nil,expectedEvent:String? = nil) {
        guard process == nil else{return};cancelledByUser=false;buffer=Data();lastError=nil;mediaResultRejected=false;workerReportedError=false;expectedWorkerEvent=expectedEvent;workerResultReceived=false
        let pipe=Pipe();let err=Pipe();p.standardOutput=pipe;p.standardError=err
        let logs=logDirectory ?? data.appendingPathComponent("Logs",isDirectory:true)
        let log=logs.appendingPathComponent("last-run.log");let archive=logs.appendingPathComponent("run-\(UUID().uuidString).log")
        var latestHandle:FileHandle?;var archiveHandle:FileHandle?
        do {
            try FileManager.default.createDirectory(at:logs,withIntermediateDirectories:true)
            try Data().write(to:log,options:.atomic);latestHandle=try FileHandle(forWritingTo:log)
            try Data().write(to:archive,options:.atomic);archiveHandle=try FileHandle(forWritingTo:archive)
        } catch {
            try? latestHandle?.close();try? archiveHandle?.close()
            stopAfterWorkerFailure();setBusy(false)
            status.stringValue="无法创建本地运行日志，任务未启动；当前文稿、录音和原文件保留。请检查 Logs 保存位置。";projectTaskFinished?();return
        }
        var logFailure:String?
        let appendLog: (Data)->Void = { [weak self] bytes in
            for handle in [latestHandle,archiveHandle] {
                do {try handle?.write(contentsOf:bytes)} catch {
                    logFailure="本地运行日志写入失败；后续操作已停止，原录音保留。请检查可用空间。"
                    self?.workerReportedError=true;self?.lastError=logFailure
                }
            }
        }
        let drained=DispatchGroup();drained.enter();drained.enter();var ioFailure:String?
        // One background reader per pipe. All UI events are queued before the
        // group completes, so trailing output cannot leak into the next task.
        let readPipe:(FileHandle,Bool)->Void = { [weak self] handle,isOutput in
            DispatchQueue.global(qos:.utility).async {
                defer {try? handle.close();drained.leave()}
                do {
                    var chunk=[UInt8](repeating:0,count:65536)
                    while true {
                        let count=chunk.withUnsafeMutableBytes {Darwin.read(handle.fileDescriptor,$0.baseAddress,$0.count)}
                        if count==0 {break}
                        if count<0 {if errno==EINTR {continue};throw NSError(domain:NSPOSIXErrorDomain,code:Int(errno))}
                        let bytes=Data(chunk.prefix(count))
                        DispatchQueue.main.async { [weak self] in
                            guard let self=self,self.process === p else{return}
                            appendLog(bytes);if isOutput {self.consume(bytes)}
                        }
                    }
                } catch {
                    DispatchQueue.main.async {ioFailure="无法完整读取本地引擎输出；原文件保留。"}
                }
            }
        }
        p.terminationHandler = { [weak self] child in drained.notify(queue:.main) {
            guard let self=self,self.process === child else{return}
            if !self.buffer.isEmpty {self.consume(Data([10]))}
            let missingResult=child.terminationStatus==0 && !self.cancelledByUser && expectedEvent != nil && !self.workerResultReceived
            if missingResult {
                self.workerReportedError=true
                if self.lastError==nil {self.lastError="本地引擎未返回任务结果；已停止后续操作，原文件保留。"}
                appendLog(Data("[App] Missing required result event: \(expectedEvent ?? "")\n".utf8))
            }
            for handle in [latestHandle,archiveHandle] {
                do {try handle?.synchronize();try handle?.close()} catch {
                    logFailure="本地运行日志保存失败；后续操作已停止，原录音保留。请检查可用空间。"
                    try? handle?.close()
                }
            }
            self.process=nil;self.setBusy(false)
            if child.terminationStatus != 0 || ioFailure != nil || logFailure != nil || self.mediaResultRejected || self.workerReportedError {self.stopAfterWorkerFailure();if !self.cancelledByUser {self.status.stringValue=ioFailure ?? logFailure ?? self.lastError ?? "生成或导入失败。已停止后续任务，原文件保留。请查看 Logs/last-run.log。"}}
            self.projectTaskFinished?()
            if !self.importing.isEmpty {let next=self.importing.removeFirst();self.run(["--import-pack",next.path])}else if let next=self.afterRun {self.afterRun=nil;next()}
        } }
        process=p;setBusy(true)
        do {try p.run();readPipe(pipe.fileHandleForReading,true);readPipe(err.fileHandleForReading,false)}
        catch {
            drained.leave();drained.leave();try? pipe.fileHandleForReading.close();try? err.fileHandleForReading.close()
            try? archiveHandle?.close();try? latestHandle?.close();process=nil;stopAfterWorkerFailure();setBusy(false);status.stringValue="无法启动本地引擎：\(error.localizedDescription)";projectTaskFinished?()
        }
    }
    func stopAfterWorkerFailure() {importing.removeAll();afterRun=nil}
    func consume(_ chunk:Data) {
        let maximumBufferedResponse=4*1024*1024
        guard buffer.count<=maximumBufferedResponse,chunk.count<=maximumBufferedResponse-buffer.count else {
            buffer.removeAll(keepingCapacity:false);workerReportedError=true
            lastError="本地引擎响应过大，已停止后续操作；当前文稿和原录音保留。";status.stringValue=lastError!;return
        }
        buffer.append(chunk);while let end=buffer.firstIndex(of:10) {let line=String(data:buffer[..<end],encoding:.utf8) ?? "";buffer.removeSubrange(...end);guard let d=line.data(using:.utf8),let obj=(try? JSONSerialization.jsonObject(with:d)) as? [String:Any],let event=obj["event"] as? String else{continue}
        // Keep terminal error/cancellation state authoritative while pipe tails drain.
        guard !cancelledByUser else{continue}
        guard !workerReportedError || event=="error" else{continue}
        // Result payloads belong to the active operation. Do not let a
        // mismatched terminal response mutate models, catalogs or recordings.
        let resultEvents:Set<String>=["health","imported","voices","complete","studio_project"]
        if let expected=expectedWorkerEvent,resultEvents.contains(event),event != expected {
            workerReportedError=true;lastError="本地引擎返回了不匹配的任务结果；当前文稿、模型状态和录音选择保留。";status.stringValue=lastError!;continue
        }
        if event==expectedWorkerEvent {
            guard !workerResultReceived else {
                workerReportedError=true;lastError="本地引擎重复返回任务结果，已保留首次结果并停止后续操作。";status.stringValue=lastError!;continue
            }
            workerResultReceived=true
        }
        if event=="studio_project" {studioProjectHandler?(obj)}
        if event=="health" || event=="imported" {
            guard let checked=obj[approvedOnly ? "identity_ready":"ready"] as? NSNumber,CFGetTypeID(checked)==CFBooleanGetTypeID() else {
                workerReportedError=true;lastError="本地引擎模型状态记录无效；当前内容和原模型保留。";status.stringValue=lastError!;continue
            }
            ready=checked.boolValue;status.stringValue=ready ? "模型已就绪。可以开始本地配音。" : (approvedOnly ? "请导入 VoiceModel-Identity.zip 和 VoiceModel-Tokenizer.zip 模型包。":"请导入 VoiceModel-Core.zip 和 VoiceModel-Tokenizer.zip 两个模型包。");generateButton.isEnabled = ready && process==nil && validDraft}
        if event=="voices" {
            guard let rows=obj["voices"] as? [[String:Any]] else {rejectVoiceCatalog();continue}
            guard let ids=validatedVoiceRowIDs(rows) else {rejectVoiceCatalog();continue}
            let total:Int
            if let raw=obj["total"] {
                guard let n=raw as? NSNumber,CFGetTypeID(n) != CFBooleanGetTypeID(),let value=raw as? Int,value>=rows.count else {rejectVoiceCatalog();continue}
                total=value
            } else {total=rows.count}
            matchTotal=total
            let oldSelection=voiceIDs.indices.contains(voicePicker.indexOfSelectedItem) ? voiceIDs[voicePicker.indexOfSelectedItem] : nil
            let previous=UserDefaults.standard.string(forKey:"studioSelectedVoice") ?? (voiceIDs.indices.contains(voicePicker.indexOfSelectedItem) ? voiceIDs[voicePicker.indexOfSelectedItem] : "natural-female")
            voiceIDs=ids;voicePicker.removeAllItems()
            let labels=["design":"设计待生成","queued":"已排队","generating":"制作中","pending_listening":"待试听","listening_confirmed":"听感已确认","similarity_flagged":"相似待复核","technical_failed":"检查失败"]
            voicePicker.addItems(withTitles:rows.map { row in (row["id"] as? String ?? "")+" · "+(row["name"] as? String ?? "")+" · "+(labels[row["status"] as? String ?? ""] ?? "设计") })
            if let index=voiceIDs.firstIndex(of:previous){voicePicker.selectItem(at:index)}
            if let summary=obj["summary"] as? [String:Any] {showSummary(summary,total:obj["total"] as? Int ?? rows.count)}
            let newSelection=voiceIDs.indices.contains(voicePicker.indexOfSelectedItem) ? voiceIDs[voicePicker.indexOfSelectedItem] : nil
            lastControlVoice=newSelection
            if oldSelection != newSelection {invalidateGeneratedAudio();lastAudition=nil;reviewButton?.isEnabled=false}
            if newSelection == nil {status.stringValue="没有匹配的声音，请修改搜索条件。"}
            else if oldSelection != newSelection {status.stringValue="已更新匹配声音；手动选择后会记住该声音。"}
            updateDraftState()
        }
        if event=="audition" {lastAudition=obj["audition"] as? [String:Any];voicedSelection=obj["voice_id"] as? String ?? "";reviewButton.isEnabled=false
            if let a=lastAudition,let e=a["evidence"] as? [String:Any],let samples=e["samples"] as? [[String:Any]],let first=samples.first,let path=first["path"] as? String {
                audioURL=URL(fileURLWithPath:path);playButton.isEnabled=true;exportButton.isEnabled=true
                reviewButton.isEnabled=(e["technical_pass"] as? Bool ?? false) && !(e["similarity_flagged"] as? Bool ?? false)
                let alert=NSAlert();alert.messageText="当前声音的两段试听";alert.informativeText="两段不同文稿用于比较身份稳定性。自动检查不能代替真实试听。\n全文检查：\(samples.allSatisfy { $0["technical_pass"] as? Bool ?? false } ? "通过" : "未通过")\n身份一致性分数：\(e["cross_text_speaker_cosine"] ?? "无")\n相似声音提示：\(e["similarity_flagged"] as? Bool ?? false ? "需复核" : "未触发")";alert.addButton(withTitle:"试听第一段");alert.addButton(withTitle:"试听第二段");alert.addButton(withTitle:"稍后")
                let answer=alert.runModal();if answer == .alertFirstButtonReturn {play()} else if answer == .alertSecondButtonReturn,samples.count>1,let second=samples[1]["path"] as? String {audioURL=URL(fileURLWithPath:second);play()}
            }else{status.stringValue="此声音尚无两段试听，请先制作。"}
        }
        if event=="audition_done" || event=="library_done" || event=="queue_saved" || event=="review_saved" {if let summary=obj["summary"] as? [String:Any] {showSummary(summary,total:matchTotal)}}
        if event=="error" {workerReportedError=true;let message=obj["message"] as? String;lastError=(message?.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty == false) ? message:"本地引擎报告错误；已停止后续任务，原文件保留。"}
        if !cancelledByUser && (event=="error" || (event=="progress" && !mediaResultRejected)) {status.stringValue=event=="error" ? (lastError ?? "本地引擎报告错误。"):(obj["message"] as? String ?? "")}
        if event=="complete" {
            guard let path=obj["path"] as? String,path.hasPrefix("/"),!path.contains("\0") else {rejectCompletedAudio();continue}
            let checked=obj["technical_pass"] as? NSNumber
            let passed=checked.map {CFGetTypeID($0)==CFBooleanGetTypeID() && $0.boolValue} ?? false
            acceptCompletedAudio(URL(fileURLWithPath:path),technicalPassed:passed)
        }
    }}
    func validatedVoiceRowIDs(_ rows:[[String:Any]])->[String]? {
        let ids=rows.compactMap{$0["id"] as? String}
        guard ids.count==rows.count,Set(ids).count==ids.count,ids.allSatisfy({!$0.isEmpty && !$0.contains("\0") && $0==$0.trimmingCharacters(in:.whitespacesAndNewlines)}) else {return nil}
        return ids
    }
    func rejectVoiceCatalog() {
        workerReportedError=true;lastError="声音列表记录无效；原文稿、声音和音频选择保留。";status.stringValue=lastError!
    }
    @discardableResult func writeNarrationRequest(_ request:[String:Any],to url:URL)->Bool {
        guard NarrationRequestParameters(request) != nil,JSONSerialization.isValidJSONObject(request) else {status.stringValue="任务参数无效；当前文稿、录音和原记录保留。";return false}
        do {try JSONSerialization.data(withJSONObject:request).write(to:url,options:.atomic);return true}
        catch {status.stringValue="任务记录保存失败，未开始配音；当前文稿、录音和原记录保留。";return false}
    }
    @discardableResult func prepareCurrentNarrationRequest(at url:URL)->Bool {
        guard process==nil,validDraft,voiceIDs.indices.contains(voicePicker.indexOfSelectedItem),NarrationRequestParameters.speeds.indices.contains(speedPicker.indexOfSelectedItem),NarrationRequestParameters.gaps.indices.contains(gapPicker.indexOfSelectedItem) else {status.stringValue="文稿或声音设置无效；原文稿和录音保留。";return false}
        let request:[String:Any] = ["text":text.string.trimmingCharacters(in:.whitespacesAndNewlines),"voice_id":voiceIDs[voicePicker.indexOfSelectedItem],"speed":NarrationRequestParameters.speeds[speedPicker.indexOfSelectedItem],"paragraph_gap":NarrationRequestParameters.gaps[gapPicker.indexOfSelectedItem]]
        guard writeNarrationRequest(request,to:url) else{return false}
        // Persist the editable original, including its whitespace, only after
        // the durable engine request is committed successfully.
        UserDefaults.standard.set(text.string,forKey:"studioNarrationDraft");UserDefaults.standard.set(url.path,forKey:"studioLastRequest");invalidateGeneratedAudio();return true
    }
    @objc func generate() {
        guard process==nil else{return};guard ready else {status.stringValue="请先导入并验证本地模型；原文稿和录音保留。";return}
        let url=data.appendingPathComponent("request-\(UUID().uuidString).json")
        guard prepareCurrentNarrationRequest(at:url) else{return}
        status.stringValue="正在准备本地录制…";run(["--generate",url.path])
    }
    func showSummary(_ summary:[String:Any],total:Int) {let states=summary["by_status"] as? [String:Int] ?? [:];if approvedOnly {librarySummary.stringValue="已确认声音 \(approvedCount) 款 · 当前匹配 \(total) 款 · 新增音色暂停";return};let queueState=(summary["paused"] as? Bool ?? false) ? ((states["generating"] ?? 0)>0 ? "当前项后暂停" : "已暂停") : "未暂停";librarySummary.stringValue="设计方案 \(summary["design_recipes"] ?? 0) · 已有两段试听 \(summary["generated_profiles"] ?? 0) · 用户听感确认 \(summary["listening_confirmed"] ?? 0) · 队列 \(summary["queue_remaining"] ?? 0)（\(queueState)） · 当前匹配 \(total)（第 \(pageOffset/150+1) 页）。方案数不等于独特合格声音数。"}
    @discardableResult func restoreNarrationParameters(_ request:[String:Any], catalogRows:[[String:Any]]? = nil)->Bool {
        guard process==nil else {status.stringValue="任务运行中，请结束后再恢复。";return false}
        guard let settings=NarrationRequestParameters(request) else {status.stringValue="原任务参数无效；当前文稿、录音和原文件保留。";return false}
        let draft=settings.draft;let voice=settings.voice;let si=settings.speedIndex;let gi=settings.gapIndex
        var allRows=catalogRows
        if allRows == nil,approvedOnly {
            guard let bytes=try? Data(contentsOf:resources.appendingPathComponent("Engine/approved-voices.json")),let object=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let voices=object["voices"] as? [[String:Any]] else {status.stringValue="已确认声音目录无法读取；当前文稿和录音保留。";return false}
            let profiles=voices.compactMap {entry -> [String:Any]? in guard var profile=entry["profile"] as? [String:Any] else{return nil};profile["status"]="listening_confirmed";return profile}
            guard profiles.count==voices.count else {status.stringValue="声音目录记录不完整；当前内容和原录音保留。";return false}
            allRows=profiles
        }
        if let allRows {
            guard validatedVoiceRowIDs(allRows) != nil,JSONSerialization.isValidJSONObject(["voices":allRows]) else {status.stringValue="声音目录记录无效；当前文稿、筛选和原录音保留。";return false}
            guard allRows.contains(where:{$0["id"] as? String == voice}) else {status.stringValue="原任务声音不在当前已确认声音中；当前内容和原文件保留。";return false}}
        else if !voiceIDs.contains(voice) {status.stringValue="原任务声音不可用；当前内容和原文件保留。";return false}
        // A validated local restore is a new operation, not a late pipe event.
        cancelledByUser=false;workerReportedError=false;mediaResultRejected=false;lastError=nil
        expectedWorkerEvent=nil;workerResultReceived=false;buffer=Data()
        if let allRows {
            searchField.stringValue="";pageOffset=0
            let event:[String:Any] = ["event":"voices","voices":allRows,"total":allRows.count]
            if var bytes=try? JSONSerialization.data(withJSONObject:event) {bytes.append(10);consume(bytes)}
            if approvedOnly {showSummary([:],total:allRows.count)}
        }
        guard let vi=voiceIDs.firstIndex(of:voice) else{return false}
        applyDraft(draft);voicePicker.selectItem(at:vi);lastControlVoice=voice;speedPicker.selectItem(at:si);gapPicker.selectItem(at:gi)
        UserDefaults.standard.set(voice,forKey:"studioSelectedVoice");settingsChanged();invalidateGeneratedAudio();updateDraftState();return true
    }
    func readNarrationRequest(_ url:URL) throws -> [String:Any] {
        let values=try url.resourceValues(forKeys:[.isRegularFileKey,.fileSizeKey])
        guard values.isRegularFile==true,let size=values.fileSize,size>0,size<=1048576 else {
            throw NSError(domain:"OfflineVoiceStudio.Request",code:1,userInfo:[NSLocalizedDescriptionKey:"任务记录不是有效的本地文件，或超过1MB；原文件保留。"])
        }
        let file=try FileHandle(forReadingFrom:url);defer{try? file.close()}
        let bytes=try file.read(upToCount:1048577) ?? Data()
        guard bytes.count<=1048576,let request=try JSONSerialization.jsonObject(with:bytes) as? [String:Any] else {
            throw NSError(domain:"OfflineVoiceStudio.Request",code:2,userInfo:[NSLocalizedDescriptionKey:"原任务记录无效；原文件保留。"])
        }
        return request
    }
    @objc func resumeLastNarration() {
        guard process==nil else{return}
        guard ready else {status.stringValue="请先导入并验证本地模型；原任务、文稿和录音保留。";return}
        guard process==nil,let path=UserDefaults.standard.string(forKey:"studioLastRequest") else{status.stringValue="还没有可恢复的任务。";return}
        do {
            let request=try readNarrationRequest(URL(fileURLWithPath:path))
            let snapshot=data.appendingPathComponent("request-resume-\(UUID().uuidString).json")
            guard writeNarrationRequest(request,to:snapshot),restoreNarrationParameters(request) else{return}
            UserDefaults.standard.set(snapshot.path,forKey:"studioLastRequest")
            status.stringValue="正在恢复原文稿、声音、语速和停顿…";run(["--generate",snapshot.path])
        } catch {status.stringValue="原任务无法读取：\(error.localizedDescription)"}
    }
    func libraryRequest(_ body:[String:Any]) {guard process==nil else{return};let url=data.appendingPathComponent("library-request-\(UUID().uuidString).json");do{try JSONSerialization.data(withJSONObject:body).write(to:url);run(["--library-request",url.path])}catch{status.stringValue=error.localizedDescription}}
    @objc func searchVoices() {guard process==nil else{return};pageOffset=0;fetchVoices()}
    @objc func previousVoices() {guard process==nil else{return};pageOffset=max(0,pageOffset-150);fetchVoices()}
    @objc func nextVoices() {guard process==nil,pageOffset+150<matchTotal else{return};pageOffset+=150;fetchVoices()}
    func fetchVoices() {guard process==nil else{return};let states=["","pending_listening","listening_confirmed","similarity_flagged","technical_failed"];let stateIndex=statePicker.indexOfSelectedItem;libraryRequest(["action":"list","query":searchField.stringValue.trimmingCharacters(in:.whitespacesAndNewlines),"state":states.indices.contains(stateIndex) ? states[stateIndex] : "","limit":150,"offset":pageOffset])}
    @objc func settingsChanged() {
        let speed=speedPicker.indexOfSelectedItem;let gap=gapPicker.indexOfSelectedItem
        guard NarrationRequestParameters.speeds.indices.contains(speed),NarrationRequestParameters.gaps.indices.contains(gap) else {status.stringValue="设置无效，原录音选择保留。";return}
        let changed=lastControlSpeed != speed || lastControlGap != gap
        lastControlSpeed=speed;lastControlGap=gap
        UserDefaults.standard.set(speed,forKey:"studioSpeedIndex");UserDefaults.standard.set(gap,forKey:"studioGapIndex")
        guard changed else {status.stringValue="语速和停顿未改变，原音频选择保留。";return}
        invalidateGeneratedAudio();status.stringValue="语速或停顿已修改；原录音保留，需重新生成才会应用新设置。"
    }
    @objc func voiceChanged() {
        guard voiceIDs.indices.contains(voicePicker.indexOfSelectedItem) else {return}
        let selected=voiceIDs[voicePicker.indexOfSelectedItem];let changed=lastControlVoice != selected
        lastControlVoice=selected;UserDefaults.standard.set(selected,forKey:"studioSelectedVoice")
        guard changed else {status.stringValue="声音未改变，原音频选择保留。";return}
        invalidateGeneratedAudio();status.stringValue="声音已切换。请点“试听已确认声音…”播放所选声音，或生成新的文稿配音。";lastAudition=nil;reviewButton?.isEnabled=false
    }
    @objc func loadAudition() {guard process==nil,voiceIDs.indices.contains(voicePicker.indexOfSelectedItem) else{return};libraryRequest(["action":"latest","voice_id":voiceIDs[voicePicker.indexOfSelectedItem]])}
    @objc func queueSelected() {guard process==nil,voiceIDs.indices.contains(voicePicker.indexOfSelectedItem) else{return};afterRun={ [weak self] in self?.resumeQueue() };libraryRequest(["action":"enqueue","voice_ids":[voiceIDs[voicePicker.indexOfSelectedItem]]])}
    @objc func queueNext() {guard process==nil else{return};let url=resources.appendingPathComponent("Engine/voice-catalog.json");guard let bytes=try? Data(contentsOf:url),let rows=(try? JSONSerialization.jsonObject(with:bytes)) as? [[String:Any]] else{return};let ids=rows.compactMap{$0["id"] as? String}.filter{$0.hasPrefix("original-")};let offset=UserDefaults.standard.integer(forKey:"voiceQueueOffset");let next=Array(ids.dropFirst(offset).prefix(16));guard !next.isEmpty else{status.stringValue="全部设计已安排；可继续保存队列。";return};UserDefaults.standard.set(offset+next.count,forKey:"voiceQueueOffset");afterRun={ [weak self] in self?.resumeQueue() };libraryRequest(["action":"enqueue","voice_ids":next])}
    @objc func resumeQueue() {guard process==nil else{return};afterRun={ [weak self] in guard let self=self else{return};self.afterRun={ [weak self] in self?.searchVoices() };self.run(["--run-library-queue"]) };libraryRequest(["action":"resume"])}
    @objc func pauseQueue() {guard process==nil else{status.stringValue="当前窗口正在运行任务，可点击取消；已生成录音保留。";return};afterRun={ [weak self] in self?.searchVoices() };libraryRequest(["action":"pause"])}
    @objc func reviewVoice() {guard process==nil,let a=lastAudition,let aid=a["id"] as? String,!voicedSelection.isEmpty else{return};let alert=NSAlert();alert.messageText="确认这款声音的当前两段试听？";alert.informativeText="只有实际听过两段、确认自然清楚且身份一致后才点通过。此确认绑定当前音频；不表示全部长文表现或与两万款声音的区分已验证。";alert.addButton(withTitle:"我已听过两段，通过");alert.addButton(withTitle:"不通过");alert.addButton(withTitle:"返回试听");let response=alert.runModal();guard response != .alertThirdButtonReturn else{return};afterRun={ [weak self] in self?.searchVoices() };libraryRequest(["action":"review","voice_id":voicedSelection,"audition_id":aid,"decision":response == .alertFirstButtonReturn ? "accept":"reject"])}
    func updateDraftState() {
        let count=text.string.unicodeScalars.count
        characterCount?.stringValue="文稿字符：\(count) / 12000"+(count>12000 ? " · 已超限，请拆分":"")
        characterCount?.textColor=count>12000 ? .systemRed:.secondaryLabelColor
        generateButton?.isEnabled=process==nil && ready && !voiceIDs.isEmpty && validDraft
    }
    func applyDraft(_ value:String) {
        window?.makeFirstResponder(text)
        let previous=text.string
        guard previous != value else {updateDraftState();status.stringValue="文稿内容相同，原稿、撤销记录和音频选择保留。";return}
        if let manager=text.undoManager {
            let openedGroup=manager.groupingLevel==0
            if openedGroup {manager.beginUndoGrouping()}
            manager.registerUndo(withTarget:self) {target in target.applyDraft(previous)};manager.setActionName("替换文稿")
            if openedGroup {manager.endUndoGrouping()}
        }
        text.string=value;UserDefaults.standard.set(value,forKey:"studioNarrationDraft");invalidateGeneratedAudio();updateDraftState();status.stringValue="文稿已更新；可在“编辑”中撤销或重做。"
    }
    @discardableResult func findDraftText(_ query:String)->Bool {
        guard !query.isEmpty else {status.stringValue="请输入要查找的文字。";return false}
        let source=text.string as NSString;let selection=text.selectedRange();let start=min(source.length,selection.location+selection.length)
        var found=source.range(of:query,options:.literal,range:NSRange(location:start,length:source.length-start))
        if found.location==NSNotFound {found=source.range(of:query,options:.literal)}
        guard found.location != NSNotFound else {status.stringValue="文稿中没有找到这段文字。";return false}
        window?.makeFirstResponder(text);text.setSelectedRange(found);text.scrollRangeToVisible(found);status.stringValue="已定位文字；再次查找会跳到下一处，末尾自动回到开头。";return true
    }
    @discardableResult func replaceDraftText(_ query:String,with replacement:String)->Int {
        guard process==nil else {status.stringValue="任务运行中，请结束后再替换文稿。";return 0}
        guard !query.isEmpty,!query.contains("\0"),!replacement.contains("\0") else {status.stringValue="查找文字不能为空，且不能包含无效字符。";return 0}
        let source=text.string;let searchable=source as NSString
        var count=0;var offset=0
        while offset<searchable.length {
            let range=searchable.range(of:query,options:.literal,range:NSRange(location:offset,length:searchable.length-offset))
            if range.location==NSNotFound {break}
            count+=1;offset=range.location+range.length
        }
        guard count>0 else {status.stringValue="文稿中没有找到这段文字；当前文稿和音频保留。";return 0}
        let resultingCount=source.unicodeScalars.count-count*query.unicodeScalars.count+count*replacement.unicodeScalars.count
        guard resultingCount<=12000 else {status.stringValue="替换后超过12000字符，未执行替换；当前文稿和音频保留。";return 0}
        let value=source.replacingOccurrences(of:query,with:replacement,options:.literal)
        guard value.unicodeScalars.count<=12000 else {status.stringValue="替换后超过12000字符，未执行替换；当前文稿和音频保留。";return 0}
        guard value != source else {status.stringValue="替换内容相同，文稿和音频保持不变。";return 0}
        applyDraft(value);status.stringValue="已替换 \(count) 处；可在“编辑”中撤销或重做。需重新生成才会应用新文稿。";return count
    }
    @objc func findReplaceDraft() {
        guard process==nil else {status.stringValue="任务运行中，请结束后再查找替换。";return}
        let alert=NSAlert();alert.messageText="查找与替换";alert.informativeText="按原文匹配，区分大小写；不使用正则表达式。全部替换可一次撤销，修改后原录音仍保留在磁盘。"
        let fields=NSStackView();fields.orientation = .vertical;fields.alignment = .leading;fields.spacing=10
        let query=NSTextField(frame:NSRect(x:0,y:0,width:420,height:24));query.placeholderString="查找文字";query.stringValue=UserDefaults.standard.string(forKey:"studioFindText") ?? ""
        let replacement=NSTextField(frame:NSRect(x:0,y:0,width:420,height:24));replacement.placeholderString="替换为（留空表示删除匹配文字）"
        fields.addArrangedSubview(query);fields.addArrangedSubview(replacement);fields.setFrameSize(NSSize(width:420,height:62));alert.accessoryView=fields
        alert.addButton(withTitle:"查找下一处");alert.addButton(withTitle:"全部替换");alert.addButton(withTitle:"取消");alert.window.initialFirstResponder=query
        let result=alert.runModal();guard result != .alertThirdButtonReturn else{return};UserDefaults.standard.set(query.stringValue,forKey:"studioFindText")
        if result == .alertFirstButtonReturn {findDraftText(query.stringValue)} else if result == .alertSecondButtonReturn {replaceDraftText(query.stringValue,with:replacement.stringValue)}
    }
    @discardableResult func importDraftFile(_ url:URL)->Bool {
        do {
            let size=(try FileManager.default.attributesOfItem(atPath:url.path)[.size] as? NSNumber)?.intValue ?? 0
            guard size<=1048576 else {status.stringValue="文稿文件过大，请拆分为不超过12000字符的TXT。原文稿保留。";return false}
            let bytes=try Data(contentsOf:url);let value:String?
            if bytes.starts(with:[0xff,0xfe]) {value=String(data:Data(bytes.dropFirst(2)),encoding:.utf16LittleEndian)}
            else if bytes.starts(with:[0xfe,0xff]) {value=String(data:Data(bytes.dropFirst(2)),encoding:.utf16BigEndian)}
            else {value=String(data:bytes.starts(with:[0xef,0xbb,0xbf]) ? Data(bytes.dropFirst(3)):bytes,encoding:.utf8)}
            guard let value=value,!value.contains("\0") else {status.stringValue="无法读取文稿编码。支持UTF-8或带BOM的UTF-16 TXT；原文稿保留。";return false}
            guard value.unicodeScalars.count<=12000 else {status.stringValue="文稿超过12000字符，请拆分；原文稿保留。";return false}
            guard !value.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else {status.stringValue="文稿为空，原文稿保留。";return false}
            if value == text.string {status.stringValue="文稿内容相同，未重复替换；音频和撤销记录保留。";return true}
            applyDraft(value);status.stringValue="文稿已导入：\(url.lastPathComponent)。可在“编辑”中撤销替换。";return true
        } catch {status.stringValue="无法读取文稿文件，原文稿保留：\(error.localizedDescription)";return false}
    }
    @objc func importText() {guard process==nil else{return};let p=NSOpenPanel();p.allowedContentTypes=[.plainText];guard p.runModal() == .OK,let url=p.url else{return};importDraftFile(url)}
    @objc func sampleText() {guard process==nil else{return};applyDraft("I left the light on for you. At first, I thought the footsteps belonged to someone upstairs. Then they stopped outside my door. I waited, listening. When I finally opened it, there was nobody there.");status.stringValue="已放入统一试听样稿；可在“编辑”中撤销替换。"}
    func playbackClock(_ value:TimeInterval)->String {guard value.isFinite,value>=0,value<Double(Int.max) else{return "0:00"};let seconds=Int(value.rounded(.down));return "\(seconds/60):"+String(format:"%02d",seconds%60)}
    func resetPlaybackSelection() {loadedAudioDuration=0;pendingPlaybackTime=0;playbackFinished=false;playbackSlider?.doubleValue=0;playbackSlider?.isEnabled=false;playbackTime?.stringValue="0:00 / 0:00";pauseButton?.isEnabled=false;stopButton?.isEnabled=false}
    @discardableResult func configurePlayback(_ url:URL)->Bool {
        guard let file=try? AVAudioFile(forReading:url),file.length>0,file.processingFormat.sampleRate.isFinite,file.processingFormat.sampleRate>0 else{return false}
        let duration=Double(file.length)/file.processingFormat.sampleRate
        guard duration.isFinite,duration>0 else{return false}
        player?.stop();player=nil;pendingPlaybackTime=0;playbackFinished=false;loadedAudioDuration=duration
        playbackSlider?.minValue=0;playbackSlider?.maxValue=max(0.001,loadedAudioDuration);playbackSlider?.doubleValue=0;playbackSlider?.isEnabled=true;pauseButton?.isEnabled=false;stopButton?.isEnabled=false;refreshPlayback();return true
    }
    @discardableResult func acceptCompletedAudio(_ url:URL,technicalPassed:Bool)->Bool {
        do {
            try validateAudioFile(url)
            guard configurePlayback(url) else {throw NSError(domain:"OfflineVoiceStudio.Audio",code:4)}
            if mediaResultRejected {lastError=nil};mediaResultRejected=false
            audioURL=url;rememberAudio(url);playButton.isEnabled=true;exportButton.isEnabled=true
            status.stringValue=String(format:technicalPassed ? "配音完成，%.1f 秒。技术检查通过，听感尚未验收。":"配音已保存，%.1f 秒。技术检查有差异待复核；未判定实际错读，听感尚未验收。",loadedAudioDuration);return true
        } catch {rejectCompletedAudio();return false}
    }
    func rejectCompletedAudio() {
        let message="无法读取完成录音；原文件和上一次有效音频选择保留。"
        mediaResultRejected=true;lastError=message;status.stringValue=message
    }
    @objc func refreshPlayback() {
        if let audio=player {pendingPlaybackTime=audio.currentTime}
        let shown=playbackFinished ? loadedAudioDuration:pendingPlaybackTime
        playbackSlider?.doubleValue=shown;playbackTime?.stringValue="\(playbackClock(shown)) / \(playbackClock(loadedAudioDuration))"
    }
    @objc func seekAudio() {guard audioURL != nil,let slider=playbackSlider,slider.doubleValue.isFinite,loadedAudioDuration.isFinite else{return};pendingPlaybackTime=min(loadedAudioDuration,max(0,slider.doubleValue));playbackFinished=false;if let audio=player {audio.currentTime=pendingPlaybackTime};refreshPlayback()}
    @objc func pauseAudio() {
        guard let audio=player else{return}
        if audio.isPlaying {audio.pause();status.stringValue="试听已暂停。"}
        else if audio.play() {status.stringValue="试听已继续。"}
        else {playbackFailed("无法继续播放，请检查系统音频输出。原录音保持不变。");return}
        refreshPlayback()
    }
    func audioPlayerDidFinishPlaying(_ audio:AVAudioPlayer,successfully flag:Bool) {
        guard audio === player else{return};player=nil;playbackFinished=flag;pendingPlaybackTime=0;pauseButton?.isEnabled=false;stopButton?.isEnabled=false;refreshPlayback();status.stringValue=flag ? "试听播放结束。":"试听意外中断，原录音保持不变。"
    }

    func playbackFailed(_ message:String) {
        player?.stop();player=nil;pendingPlaybackTime=0;playbackFinished=false;pauseButton?.isEnabled=false;stopButton?.isEnabled=false;refreshPlayback();status.stringValue=message
    }
    func audioPlayerDecodeErrorDidOccur(_ audio:AVAudioPlayer,error:Error?) {
        guard audio === player else{return};playbackFailed("试听解码失败；原录音保留，可重新打开有效录音。")
    }

    func configureSaveLocation(_ panel:NSSavePanel) {
        let desktop=FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Desktop",isDirectory:true)
        if let parent=lastSavedURL?.deletingLastPathComponent(),let values=try? parent.resourceValues(forKeys:[.isDirectoryKey]),values.isDirectory==true {panel.directoryURL=parent}
        else {panel.directoryURL=desktop}
    }
    func savedFile(_ target:URL, kind:String) {lastSavedURL=target;UserDefaults.standard.set(target.path,forKey:"studioLastSavedFile");revealSavedButton.isEnabled=true;status.stringValue="\(kind)：\(target.path)";status.toolTip=target.path}
    @objc func revealSavedFile() {guard let target=lastSavedURL else{return};guard FileManager.default.fileExists(atPath:target.path) else {status.stringValue="上次保存的文件已移动或删除，原记录保留：\(target.path)";return};NSWorkspace.shared.activateFileViewerSelecting([target])}
    func protectedSaveTarget(_ target:URL)->Bool {
        // Foundation leaves intermediate symlinks unresolved if the final file
        // does not exist. Resolve the closest existing parent before appending
        // the new filename so a library alias cannot bypass file protection.
        var parent=target.standardizedFileURL;var remaining=[String]()
        while parent.path != "/" && !FileManager.default.fileExists(atPath:parent.path) {
            remaining.append(parent.lastPathComponent);parent.deleteLastPathComponent()
        }
        var resolved=parent.resolvingSymlinksInPath().standardizedFileURL
        for component in remaining.reversed() {resolved.appendPathComponent(component)}
        let root=data.resolvingSymlinksInPath().standardizedFileURL.path
        return resolved.path==root || resolved.path.hasPrefix(root+"/") || resolved.pathComponents.contains(where:{$0.lowercased().hasSuffix(".app")}) || (projectEditor?.protectedAudioTarget(target) ?? false)
    }
    @discardableResult func saveDraftFile(_ target:URL)->Bool {
        guard !protectedSaveTarget(target),audioURL.map({!sameFile($0,target)}) ?? true else {status.stringValue="请选择其他保存位置；不能覆盖原录音、模型或应用内部文件。";return false}
        do {try text.string.write(to:target,atomically:true,encoding:.utf8);savedFile(target,kind:"文稿已保存");return true}
        catch {status.stringValue="文稿保存失败；原文稿和已有文件保留：\(error.localizedDescription)";return false}
    }
    @objc func saveText() {let panel=NSSavePanel();configureSaveLocation(panel);panel.nameFieldStringValue="配音文稿.txt";panel.allowedContentTypes=[.plainText];guard panel.runModal() == .OK,let target=panel.url else{return};saveDraftFile(target)}
    @objc func stopAudio() {player?.stop();player?.currentTime=0;pendingPlaybackTime=0;playbackFinished=false;pauseButton?.isEnabled=false;stopButton?.isEnabled=false;refreshPlayback();status.stringValue="试听已停止并回到开头。"}
    func validateAudioFile(_ url:URL) throws {
        let file=try AVAudioFile(forReading:url,commonFormat:.pcmFormatFloat32,interleaved:false)
        guard file.length>0, file.processingFormat.sampleRate.isFinite, file.processingFormat.sampleRate>0,
              let buffer=AVAudioPCMBuffer(pcmFormat:file.processingFormat,frameCapacity:8192) else {
            throw NSError(domain:"OfflineVoiceStudio.Audio",code:1,userInfo:[NSLocalizedDescriptionKey:"录音为空或格式无效。"])
        }
        var readFrames:AVAudioFramePosition=0
        while readFrames<file.length {
            try file.read(into:buffer,frameCount:AVAudioFrameCount(min(8192,file.length-readFrames)))
            guard buffer.frameLength>0 else {throw NSError(domain:"OfflineVoiceStudio.Audio",code:2,userInfo:[NSLocalizedDescriptionKey:"录音未能完整读取。"])}
            guard let channels=buffer.floatChannelData else {throw NSError(domain:"OfflineVoiceStudio.Audio",code:3)}
            for channel in 0..<Int(buffer.format.channelCount) {
                for frame in 0..<Int(buffer.frameLength) where !channels[channel][frame].isFinite {
                    throw NSError(domain:"OfflineVoiceStudio.Audio",code:3,userInfo:[NSLocalizedDescriptionKey:"录音含有无效采样值，未播放或导出。"])
                }
            }
            readFrames += AVAudioFramePosition(buffer.frameLength)
        }
    }
    @objc func openAudio() {
        guard process==nil else {status.stringValue="任务运行中，请结束后再打开其他录音。";return}
        let p=NSOpenPanel();p.allowedContentTypes=[.wav];p.directoryURL=UserDefaults.standard.string(forKey:"studioLastOpenedAudio").map {URL(fileURLWithPath:$0).deletingLastPathComponent()} ?? data.appendingPathComponent("Recordings")
        guard p.runModal() == .OK,let url=p.url else{return}
        loadExistingAudio(url)
    }
    @discardableResult func loadExistingAudio(_ url:URL)->Bool {
        guard process==nil else {status.stringValue="任务运行中，请结束后再打开其他录音。";return false}
        do {try validateAudioFile(url);guard configurePlayback(url) else {throw NSError(domain:"OfflineVoiceStudio.Audio",code:4)};audioURL=url;rememberAudio(url);playButton.isEnabled=true;exportButton.isEnabled=true;status.stringValue="已打开录音：\(url.path)";status.toolTip=url.path;return true}
        catch {NSLog("Open audio failed: %@",error.localizedDescription);status.stringValue="无法打开录音，文件可能已损坏或无法读取。原文件与上一次有效选择保留。";return false}
    }
    func recentAudioPaths()->[String] {
        var paths=UserDefaults.standard.stringArray(forKey:"studioRecentAudio") ?? []
        if let last=UserDefaults.standard.string(forKey:"studioLastOpenedAudio"),!paths.contains(last) {paths.insert(last,at:0)}
        var seen=Set<String>()
        return Array(paths.compactMap { path -> String? in
            guard path.hasPrefix("/"),!path.contains("\0") else{return nil}
            let normalized=URL(fileURLWithPath:path).standardizedFileURL.path
            guard seen.insert(normalized).inserted else{return nil};return normalized
        }.prefix(10))
    }
    func rememberAudio(_ url:URL) {
        UserDefaults.standard.set(url.path,forKey:"studioLastOpenedAudio")
        var paths=recentAudioPaths().filter {$0 != url.path};paths.insert(url.path,at:0);UserDefaults.standard.set(Array(paths.prefix(10)),forKey:"studioRecentAudio")
    }
    @objc func chooseRecentAudio() {
        guard process==nil else{return};let paths=recentAudioPaths()
        guard !paths.isEmpty else {status.stringValue="还没有最近录音，请先打开已有录音。";return}
        let alert=NSAlert();alert.messageText="最近打开的录音";alert.informativeText="只打开本地已有文件，不生成或识别；移动或删除过的文件会提示读取失败。"
        let picker=NSPopUpButton(frame:NSRect(x:0,y:0,width:600,height:28));picker.addItems(withTitles:paths.map {let u=URL(fileURLWithPath:$0);return u.lastPathComponent+" — "+u.deletingLastPathComponent().path});alert.accessoryView=picker;alert.addButton(withTitle:"打开");alert.addButton(withTitle:"取消")
        guard alert.runModal() == .alertFirstButtonReturn,paths.indices.contains(picker.indexOfSelectedItem) else{return};loadExistingAudio(URL(fileURLWithPath:paths[picker.indexOfSelectedItem]))
    }
    @objc func openRecentAudio() {
        guard process==nil else{return}
        guard let path=UserDefaults.standard.string(forKey:"studioLastOpenedAudio") else {status.stringValue="还没有最近打开的录音，请先点“打开已有录音…”。";return}
        loadExistingAudio(URL(fileURLWithPath:path))
    }
    @objc func cancel() {guard let active=process else{return};cancelledByUser=true;let modelImport = process?.arguments?.contains("--import-pack") == true;afterRun=nil;importing=[];if active.isRunning {active.terminate()};status.stringValue=modelImport ? "模型导入已取消。请重新选择原模型包导入；已验证的文件保留。" : "已取消。已完成分段保留；同一文稿和设置再次生成可恢复。"}
    @objc func importModels() {guard process==nil else {status.stringValue="任务运行中，不能另行安排模型导入。";return};let panel=NSOpenPanel();panel.title="选择模型 ZIP 包（支持分批导入）";panel.allowedContentTypes=[.zip];panel.allowsMultipleSelection=true;guard panel.runModal() == .OK else{return};importing=panel.urls;if !importing.isEmpty {let first=importing.removeFirst();status.stringValue="正在导入并校验模型…";run(["--import-pack",first.path])}}
    @objc func playApprovedAudition() {
        guard process==nil,voiceIDs.indices.contains(voicePicker.indexOfSelectedItem) else{return}
        do {
            let object=try JSONSerialization.jsonObject(with:Data(contentsOf:resources.appendingPathComponent("Engine/approved-voices.json"))) as? [String:Any]
            let voices=object?["voices"] as? [[String:Any]] ?? []
            guard let voice=voices.first(where:{$0["id"] as? String == voiceIDs[voicePicker.indexOfSelectedItem]}),let samples=voice["auditions"] as? [[String:String]],samples.count>=2 else {status.stringValue="当前声音没有随包试听，原录音保留。";return}
            let alert=NSAlert();alert.messageText="播放已确认声音的原试听";alert.informativeText="复用已入库录音，不生成、不识别。此次播放不代表新文稿或长文听感验收。";alert.addButton(withTitle:"第一段");alert.addButton(withTitle:"第二段");alert.addButton(withTitle:"取消")
            let answer=alert.runModal();guard answer != .alertThirdButtonReturn else{return};let sample=samples[answer == .alertFirstButtonReturn ? 0:1]
            guard let path=sample["path"],let hash=sample["sha256"],path.hasPrefix("approved-auditions/"),!path.contains("..") else {status.stringValue="试听记录无效。";return}
            let url=resources.appendingPathComponent("Engine").appendingPathComponent(path);let bytes=try Data(contentsOf:url);guard SHA256.hash(data:bytes).map({String(format:"%02x",$0)}).joined()==hash else {status.stringValue="试听文件校验失败，未播放。";return}
            try validateAudioFile(url);guard configurePlayback(url) else {throw NSError(domain:"OfflineVoiceStudio.Audio",code:4)};audioURL=url;playButton.isEnabled=true;exportButton.isEnabled=true;guard startPlayback() else{return};status.stringValue="正在播放已入库原试听：\(voiceIDs[voicePicker.indexOfSelectedItem])。未生成新录音。"
        } catch {status.stringValue="无法读取随包试听：\(error.localizedDescription)"}
    }
    @discardableResult func startPlayback()->Bool {
        guard let url=audioURL else{return false}
        do {
            let audio=try AVAudioPlayer(contentsOf:url)
            guard audio.duration.isFinite,audio.duration>0 else {throw NSError(domain:"OfflineVoiceStudio.Audio",code:4)}
            let position=pendingPlaybackTime.isFinite ? max(0,pendingPlaybackTime):0
            audio.delegate=self;audio.currentTime=playbackFinished ? 0:min(position,max(0,audio.duration-0.001))
            guard audio.prepareToPlay() else {playbackFailed("无法准备播放，请检查系统音频输出。原录音保持不变。");return false}
            player?.stop()
            guard audio.play() else {playbackFailed("无法开始播放，请检查系统音频输出。原录音保持不变。");return false}
            player=audio;playbackFinished=false;loadedAudioDuration=audio.duration;playbackSlider?.minValue=0;playbackSlider?.maxValue=max(0.001,loadedAudioDuration);pauseButton?.isEnabled=true;stopButton?.isEnabled=true;refreshPlayback();return true
        } catch {playbackFailed("播放失败：\(error.localizedDescription)");return false}
    }
    @objc func play() {if startPlayback(),let url=audioURL {status.stringValue="正在播放：\(url.lastPathComponent)"}}

    func sameFile(_ a:URL,_ b:URL)->Bool {
        if a.resolvingSymlinksInPath().standardizedFileURL.path==b.resolvingSymlinksInPath().standardizedFileURL.path {return true}
        guard let x=try? FileManager.default.attributesOfItem(atPath:a.path),let y=try? FileManager.default.attributesOfItem(atPath:b.path),let xd=x[.systemNumber] as? NSNumber,let xi=x[.systemFileNumber] as? NSNumber,let yd=y[.systemNumber] as? NSNumber,let yi=y[.systemFileNumber] as? NSNumber else{return false}
        return xd==yd && xi==yi
    }
    func stageWAVExport(_ source:URL,to target:URL) throws {
        let temporary=target.deletingLastPathComponent().appendingPathComponent(".voice-export-\(UUID().uuidString).wav")
        defer {try? FileManager.default.removeItem(at:temporary)}
        let header:Data
        do {let handle=try FileHandle(forReadingFrom:source);defer{try? handle.close()};header=try handle.read(upToCount:12) ?? Data()}
        let marker=header.count>=12 ? String(data:header.suffix(4),encoding:.ascii):nil
        let kind=header.count>=4 ? String(data:header.prefix(4),encoding:.ascii):nil
        if marker=="WAVE",let kind=kind,["RIFF","RIFX","RF64","BW64"].contains(kind) {
            // A filesystem copy avoids buffering a whole long recording in RAM.
            try FileManager.default.copyItem(at:source,to:temporary)
        } else {
            // A renamed AIFF/CAF is decodable but cannot be exported as WAV by
            // changing its suffix. Preserve decoded precision in a float WAV.
            let input=try AVAudioFile(forReading:source,commonFormat:.pcmFormatFloat32,interleaved:false)
            let format=input.processingFormat
            let settings:[String:Any]=[AVFormatIDKey:kAudioFormatLinearPCM,AVSampleRateKey:format.sampleRate,AVNumberOfChannelsKey:format.channelCount,AVLinearPCMBitDepthKey:32,AVLinearPCMIsFloatKey:true,AVLinearPCMIsBigEndianKey:false,AVLinearPCMIsNonInterleaved:false]
            do {
                let output=try AVAudioFile(forWriting:temporary,settings:settings,commonFormat:.pcmFormatFloat32,interleaved:false)
                guard let block=AVAudioPCMBuffer(pcmFormat:format,frameCapacity:8192) else {throw NSError(domain:"OfflineVoiceStudio.Export",code:1)}
                var frames:AVAudioFramePosition=0
                while frames<input.length {try input.read(into:block,frameCount:AVAudioFrameCount(min(8192,input.length-frames)));guard block.frameLength>0 else {throw NSError(domain:"OfflineVoiceStudio.Export",code:2)};try output.write(from:block);frames += AVAudioFramePosition(block.frameLength)}
            }
        }
        try validateAudioFile(temporary)
        guard Darwin.rename(temporary.path,target.path)==0 else {throw NSError(domain:NSPOSIXErrorDomain,code:Int(errno))}
    }
    @discardableResult func exportAudioFile(_ target:URL)->Bool {
        guard let source=audioURL else{return false}
        guard !sameFile(source,target),!protectedSaveTarget(target) else {status.stringValue="请选择其他文件名或保存位置；不能覆盖当前工程的原录音、历史音频、模型或应用内部文件。";return false}
        do {try validateAudioFile(source);try stageWAVExport(source,to:target);savedFile(target,kind:"录音已导出");return true}
        catch {NSLog("Export audio failed: %@",error.localizedDescription);status.stringValue="导出失败：录音或保存位置无法读取或写入。原录音保持不变。";return false}
    }
    @objc func exportAudio() {guard audioURL != nil else{return};let panel=NSSavePanel();configureSaveLocation(panel);panel.nameFieldStringValue="配音.wav";panel.allowedContentTypes=[.wav];guard panel.runModal() == .OK,let target=panel.url else{return};exportAudioFile(target)}
    @objc func openProjectStudio(){guard process==nil else{return};if projectEditor==nil{projectEditor=ProjectEditor(owner:self)};projectEditor?.show()}
    @objc func openRecordings() {let url=data.appendingPathComponent("Recordings",isDirectory:true);try? FileManager.default.createDirectory(at:url,withIntermediateDirectories:true);NSWorkspace.shared.open(url)}
    @objc func showAbout() {let alert=NSAlert();alert.messageText="离线配音工作室 \(Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "")";alert.informativeText="独立 macOS 本地语音工具\n应用专有代码保留所有权利，不开放源码。\n使用 Qwen3-TTS、MLX 等第三方组件，许可随包保留。\nApple 芯片 · 无账号 · 无云端回退";alert.addButton(withTitle:"好");alert.addButton(withTitle:"第三方许可");if alert.runModal() == .alertSecondButtonReturn {NSWorkspace.shared.open(resources.appendingPathComponent("Licenses"))}}
    func showMainWindow() {window?.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)}
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool)->Bool {if !flag {showMainWindow()};return true}
    func applicationShouldTerminate(_ sender:NSApplication)->NSApplication.TerminateReply {
        if process==nil,let editor=projectEditor,!editor.commitClip(){editor.show();return .terminateCancel}
        if process != nil {
            let a=NSAlert();a.messageText="任务尚未结束，退出会取消当前操作。";a.addButton(withTitle:"继续运行");a.addButton(withTitle:"退出")
            if a.runModal() == .alertFirstButtonReturn {showMainWindow();return .terminateCancel}
            cancel()
        }
        return .terminateNow
    }
    func applicationWillTerminate(_ notification:Notification) {playbackTimer?.invalidate();player?.stop()}
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool {true}
}
