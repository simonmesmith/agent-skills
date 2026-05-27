import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

enum RecorderError: Error, CustomStringConvertible {
    case usage
    case unsupportedOS
    case noDisplay
    case permissionDenied(String)
    case recordingFailed(String)
    case unsupportedAudioFormat(String)

    var description: String {
        switch self {
        case .usage:
            return "Usage: codex-meeting-recorder record --out <path.mp4> [--no-system-audio] [--no-microphone]\n       codex-meeting-recorder stream-pcm [--no-system-audio] [--no-microphone]\n       codex-meeting-recorder stream-pcm-json [--no-system-audio] [--no-microphone]\n       codex-meeting-recorder probe-audio [--duration <seconds>] [--no-system-audio] [--no-microphone]"
        case .unsupportedOS:
            return "Codex Meeting Recorder requires macOS 15 or newer."
        case .noDisplay:
            return "No capturable display was found."
        case .permissionDenied(let detail):
            return detail
        case .recordingFailed(let detail):
            return detail
        case .unsupportedAudioFormat(let detail):
            return detail
        }
    }
}

final class RecordingDelegate: NSObject, SCRecordingOutputDelegate, SCStreamDelegate {
    private struct State {
        var didStart = false
        var didFinish = false
        var failure: Error?
    }

    private let stateQueue = DispatchQueue(label: "codex-meeting-recorder.delegate-state")
    private var state = State()

    func recordingOutputDidStartRecording(_ recordingOutput: SCRecordingOutput) {
        stateQueue.sync {
            state.didStart = true
        }
        fputs("recording_started\n", stderr)
    }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        stateQueue.sync {
            state.failure = error
            state.didFinish = true
        }
        fputs("recording_failed: \(error.localizedDescription)\n", stderr)
    }

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        stateQueue.sync {
            state.didFinish = true
        }
        fputs("recording_finished\n", stderr)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        stateQueue.sync {
            state.failure = error
            state.didFinish = true
        }
        fputs("stream_stopped_with_error: \(error.localizedDescription)\n", stderr)
    }

    func waitForStart(timeoutSeconds: TimeInterval) async throws {
        let deadline = Date().addingTimeInterval(timeoutSeconds)
        while Date() < deadline {
            let snapshot = stateQueue.sync { state }
            let started = snapshot.didStart
            let error = snapshot.failure
            if let error {
                throw error
            }
            if started {
                return
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
    }

    func waitForFinish(timeoutSeconds: TimeInterval) async throws {
        let deadline = Date().addingTimeInterval(timeoutSeconds)
        while Date() < deadline {
            let snapshot = stateQueue.sync { state }
            let finished = snapshot.didFinish
            let error = snapshot.failure
            if let error {
                throw error
            }
            if finished {
                return
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
    }
}

struct Arguments {
    var mode = "record"
    var outputPath: String?
    var captureSystemAudio = true
    var captureMicrophone = true
    var durationSeconds = 3.0
}

func parseArguments(_ args: [String]) throws -> Arguments {
    guard let mode = args.first, ["record", "stream-pcm", "stream-pcm-json", "probe-audio"].contains(mode) else {
        throw RecorderError.usage
    }

    var parsed = Arguments(mode: mode)
    var index = 1
    while index < args.count {
        let arg = args[index]
        switch arg {
        case "--out":
            index += 1
            guard index < args.count else { throw RecorderError.usage }
            parsed.outputPath = args[index]
        case "--no-system-audio":
            parsed.captureSystemAudio = false
        case "--no-microphone":
            parsed.captureMicrophone = false
        case "--duration":
            index += 1
            guard index < args.count, let duration = Double(args[index]), duration > 0 else { throw RecorderError.usage }
            parsed.durationSeconds = duration
        case "--help", "-h":
            throw RecorderError.usage
        default:
            throw RecorderError.usage
        }
        index += 1
    }

    guard parsed.mode != "record" || parsed.outputPath != nil else {
        throw RecorderError.usage
    }
    return parsed
}

struct AudioProbeResult: Codable {
    let source: String
    let capturedBytes: Int
    let sampleCount: Int
    let rms: Double
    let peak: Double
}

final class AudioProbeOutput: NSObject, SCStreamOutput {
    private let source: String
    private let statsLock = NSLock()
    private var sampleCount = 0
    private var sumSquares = 0.0
    private var peak = 0.0

    init(source: String) {
        self.source = source
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard CMSampleBufferDataIsReady(sampleBuffer), CMSampleBufferGetNumSamples(sampleBuffer) > 0 else {
            return
        }

        do {
            try collect(from: sampleBuffer)
        } catch {
            if let recorderError = error as? RecorderError {
                fputs("audio_probe_sample_failed: \(source): \(recorderError.description)\n", stderr)
            } else {
                fputs("audio_probe_sample_failed: \(source): \(error.localizedDescription)\n", stderr)
            }
        }
    }

    func result() -> AudioProbeResult {
        statsLock.lock()
        let count = sampleCount
        let squares = sumSquares
        let localPeak = peak
        statsLock.unlock()

        let rms = count > 0 ? sqrt(squares / Double(count)) * Double(Int16.max) : 0
        return AudioProbeResult(
            source: source,
            capturedBytes: count * MemoryLayout<Int16>.size,
            sampleCount: count,
            rms: rms,
            peak: localPeak * Double(Int16.max)
        )
    }

    private func collect(from sampleBuffer: CMSampleBuffer) throws {
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let streamDescriptionPointer = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription) else {
            throw RecorderError.unsupportedAudioFormat("Missing audio stream description.")
        }

        let streamDescription = streamDescriptionPointer.pointee
        let channelCount = max(1, Int(streamDescription.mChannelsPerFrame))
        let frameCount = CMSampleBufferGetNumSamples(sampleBuffer)
        var listSize = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: &listSize,
            bufferListOut: nil,
            bufferListSize: 0,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: nil
        )
        if listSize <= 0 {
            listSize = MemoryLayout<AudioBufferList>.size + max(0, channelCount - 1) * MemoryLayout<AudioBuffer>.size
        }
        let rawAudioBufferList = UnsafeMutableRawPointer.allocate(
            byteCount: listSize,
            alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer { rawAudioBufferList.deallocate() }
        let audioBufferList = rawAudioBufferList.bindMemory(to: AudioBufferList.self, capacity: 1)

        var blockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: audioBufferList,
            bufferListSize: listSize,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer
        )
        guard status == noErr else {
            throw RecorderError.unsupportedAudioFormat("Could not read audio buffer list: \(status).")
        }

        let buffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
        let flags = streamDescription.mFormatFlags
        let isFloat = (flags & kAudioFormatFlagIsFloat) != 0
        let isSignedInteger = (flags & kAudioFormatFlagIsSignedInteger) != 0
        let isNonInterleaved = (flags & kAudioFormatFlagIsNonInterleaved) != 0
        let bits = Int(streamDescription.mBitsPerChannel)
        let bytesPerSample = max(1, bits / 8)
        let bytesPerFrame = isNonInterleaved
            ? max(bytesPerSample, Int(streamDescription.mBytesPerFrame))
            : max(bytesPerSample * channelCount, Int(streamDescription.mBytesPerFrame))

        var localCount = 0
        var localSquares = 0.0
        var localPeak = 0.0

        for frameIndex in 0..<frameCount {
            var total = 0.0
            for channel in 0..<channelCount {
                let bufferIndex = isNonInterleaved ? min(channel, buffers.count - 1) : 0
                guard let rawPointer = buffers[bufferIndex].mData else { continue }
                let channelOffset = isNonInterleaved ? 0 : channel * bytesPerSample
                let byteOffset = frameIndex * bytesPerFrame + channelOffset
                let pointer = rawPointer.advanced(by: byteOffset)

                if isFloat && bits == 32 {
                    total += Double(pointer.assumingMemoryBound(to: Float.self).pointee)
                } else if isSignedInteger && bits == 16 {
                    total += Double(Int16(littleEndian: pointer.assumingMemoryBound(to: Int16.self).pointee)) / Double(Int16.max)
                } else if isSignedInteger && bits == 32 {
                    total += Double(Int32(littleEndian: pointer.assumingMemoryBound(to: Int32.self).pointee)) / Double(Int32.max)
                }
            }
            let sample = total / Double(channelCount)
            let clamped = max(-1.0, min(1.0, sample))
            localCount += 1
            localSquares += clamped * clamped
            localPeak = max(localPeak, abs(clamped))
        }

        statsLock.lock()
        sampleCount += localCount
        sumSquares += localSquares
        peak = max(peak, localPeak)
        statsLock.unlock()
    }
}

func requestMicrophonePermissionIfNeeded() async -> Bool {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized:
        return true
    case .notDetermined:
        return await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
    default:
        return false
    }
}

func requestScreenCapturePermissionIfNeeded() -> Bool {
    if CGPreflightScreenCaptureAccess() {
        return true
    }
    return CGRequestScreenCaptureAccess()
}

func installSignalHandler() {
    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)

    let signalQueue = DispatchQueue(label: "codex-meeting-recorder.signals")
    let interruptSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: signalQueue)
    let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: signalQueue)
    let semaphore = SignalBox.shared

    interruptSource.setEventHandler {
        semaphore.stop()
    }
    termSource.setEventHandler {
        semaphore.stop()
    }
    interruptSource.resume()
    termSource.resume()
    SignalBox.shared.interruptSource = interruptSource
    SignalBox.shared.keepAlive = termSource
}

final class SignalBox {
    static let shared = SignalBox()
    var interruptSource: DispatchSourceSignal?
    var keepAlive: DispatchSourceSignal?
    private let continuationLock = NSLock()
    private var continuation: CheckedContinuation<Void, Never>?

    func wait() async {
        await withCheckedContinuation { continuation in
            continuationLock.lock()
            self.continuation = continuation
            continuationLock.unlock()
        }
    }

    func stop() {
        continuationLock.lock()
        let continuation = self.continuation
        self.continuation = nil
        continuationLock.unlock()
        continuation?.resume()
    }
}

final class PCMStreamOutput: NSObject, SCStreamOutput {
    private static let outputLock = NSLock()
    private let outputRate = 24_000.0
    private let output = FileHandle.standardOutput
    private let source: String
    private let tagged: Bool
    private var sourcePosition = 0.0

    init(source: String, tagged: Bool = false) {
        self.source = source
        self.tagged = tagged
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard CMSampleBufferDataIsReady(sampleBuffer), CMSampleBufferGetNumSamples(sampleBuffer) > 0 else {
            return
        }

        do {
            let data = try pcm16Mono24k(from: sampleBuffer)
            guard !data.isEmpty else { return }
            PCMStreamOutput.outputLock.lock()
            if tagged {
                let payload: [String: Any] = [
                    "source": source,
                    "timestamp": Date().timeIntervalSince1970,
                    "audio": data.base64EncodedString()
                ]
                if let json = try? JSONSerialization.data(withJSONObject: payload) {
                    output.write(json)
                    output.write(Data("\n".utf8))
                }
            } else {
                output.write(data)
            }
            PCMStreamOutput.outputLock.unlock()
        } catch {
            if let recorderError = error as? RecorderError {
                fputs("audio_sample_failed: \(recorderError.description)\n", stderr)
            } else {
                fputs("audio_sample_failed: \(error.localizedDescription)\n", stderr)
            }
        }
    }

    private func pcm16Mono24k(from sampleBuffer: CMSampleBuffer) throws -> Data {
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let streamDescriptionPointer = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription) else {
            throw RecorderError.unsupportedAudioFormat("Missing audio stream description.")
        }

        let streamDescription = streamDescriptionPointer.pointee
        let channelCount = max(1, Int(streamDescription.mChannelsPerFrame))
        let frameCount = CMSampleBufferGetNumSamples(sampleBuffer)
        var listSize = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: &listSize,
            bufferListOut: nil,
            bufferListSize: 0,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: nil
        )
        if listSize <= 0 {
            listSize = MemoryLayout<AudioBufferList>.size + max(0, channelCount - 1) * MemoryLayout<AudioBuffer>.size
        }
        let rawAudioBufferList = UnsafeMutableRawPointer.allocate(
            byteCount: listSize,
            alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer { rawAudioBufferList.deallocate() }
        let audioBufferList = rawAudioBufferList.bindMemory(to: AudioBufferList.self, capacity: 1)

        var blockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: audioBufferList,
            bufferListSize: listSize,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer
        )
        guard status == noErr else {
            throw RecorderError.unsupportedAudioFormat("Could not read audio buffer list: \(status).")
        }

        let buffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
        let ratio = max(1.0, streamDescription.mSampleRate / outputRate)
        var data = Data()
        data.reserveCapacity(Int(Double(frameCount) / ratio) * 2)

        while sourcePosition < Double(frameCount) {
            let frameIndex = Int(sourcePosition)
            let sample = sampleAt(
                frameIndex: frameIndex,
                channelCount: channelCount,
                buffers: buffers,
                streamDescription: streamDescription
            )
            let clamped = max(-1.0, min(1.0, sample))
            var intSample = Int16(clamped * Double(Int16.max)).littleEndian
            withUnsafeBytes(of: &intSample) { data.append(contentsOf: $0) }
            sourcePosition += ratio
        }
        sourcePosition -= Double(frameCount)
        return data
    }

    private func sampleAt(
        frameIndex: Int,
        channelCount: Int,
        buffers: UnsafeMutableAudioBufferListPointer,
        streamDescription: AudioStreamBasicDescription
    ) -> Double {
        let flags = streamDescription.mFormatFlags
        let isFloat = (flags & kAudioFormatFlagIsFloat) != 0
        let isSignedInteger = (flags & kAudioFormatFlagIsSignedInteger) != 0
        let isNonInterleaved = (flags & kAudioFormatFlagIsNonInterleaved) != 0
        let bits = Int(streamDescription.mBitsPerChannel)
        let bytesPerSample = max(1, bits / 8)
        let bytesPerFrame = isNonInterleaved
            ? max(bytesPerSample, Int(streamDescription.mBytesPerFrame))
            : max(bytesPerSample * channelCount, Int(streamDescription.mBytesPerFrame))

        var total = 0.0
        for channel in 0..<channelCount {
            let bufferIndex = isNonInterleaved ? min(channel, buffers.count - 1) : 0
            guard let rawPointer = buffers[bufferIndex].mData else { continue }
            let channelOffset = isNonInterleaved ? 0 : channel * bytesPerSample
            let byteOffset = frameIndex * bytesPerFrame + channelOffset
            let pointer = rawPointer.advanced(by: byteOffset)

            if isFloat && bits == 32 {
                total += Double(pointer.assumingMemoryBound(to: Float.self).pointee)
            } else if isSignedInteger && bits == 16 {
                total += Double(Int16(littleEndian: pointer.assumingMemoryBound(to: Int16.self).pointee)) / Double(Int16.max)
            } else if isSignedInteger && bits == 32 {
                total += Double(Int32(littleEndian: pointer.assumingMemoryBound(to: Int32.self).pointee)) / Double(Int32.max)
            }
        }
        return total / Double(channelCount)
    }
}

@main
struct CodexMeetingRecorder {
    static func main() async {
        do {
            try await run()
        } catch let error as RecorderError {
            fputs("\(error.description)\n", stderr)
            exit(error.description == RecorderError.usage.description ? 64 : 1)
        } catch {
            fputs("Codex Meeting Recorder failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }

    static func run() async throws {
        guard #available(macOS 15.0, *) else {
            throw RecorderError.unsupportedOS
        }

        let args = try parseArguments(Array(CommandLine.arguments.dropFirst()))

        if args.captureSystemAudio && !requestScreenCapturePermissionIfNeeded() {
            throw RecorderError.permissionDenied("Screen Recording permission is required for system audio. Grant it in System Settings, restart Codex/Terminal if macOS asks, then retry.")
        }

        if args.captureMicrophone {
            let micGranted = await requestMicrophonePermissionIfNeeded()
            if !micGranted {
                throw RecorderError.permissionDenied("Microphone permission is required. Grant it in System Settings, restart Codex/Terminal if macOS asks, then retry.")
            }
        }

        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw RecorderError.noDisplay
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.width = 1280
        configuration.height = 720
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.queueDepth = 3
        configuration.showsCursor = false
        configuration.capturesAudio = args.captureSystemAudio
        configuration.sampleRate = 48_000
        configuration.channelCount = 2
        configuration.excludesCurrentProcessAudio = true
        configuration.captureMicrophone = args.captureMicrophone

        if args.mode == "stream-pcm" || args.mode == "stream-pcm-json" {
            try await streamPCM(filter: filter, configuration: configuration, tagged: args.mode == "stream-pcm-json")
            return
        }

        if args.mode == "probe-audio" {
            try await probeAudio(filter: filter, configuration: configuration, durationSeconds: args.durationSeconds)
            return
        }

        guard let outputPath = args.outputPath else {
            throw RecorderError.usage
        }
        let outputURL = URL(fileURLWithPath: outputPath)
        try FileManager.default.createDirectory(
            at: outputURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        if FileManager.default.fileExists(atPath: outputURL.path) {
            try FileManager.default.removeItem(at: outputURL)
        }

        let delegate = RecordingDelegate()
        let stream = SCStream(filter: filter, configuration: configuration, delegate: delegate)

        let recordingConfiguration = SCRecordingOutputConfiguration()
        recordingConfiguration.outputURL = outputURL
        recordingConfiguration.outputFileType = .mp4
        recordingConfiguration.videoCodecType = .h264

        let recordingOutput = SCRecordingOutput(configuration: recordingConfiguration, delegate: delegate)
        try stream.addRecordingOutput(recordingOutput)

        installSignalHandler()
        try await stream.startCapture()
        try await delegate.waitForStart(timeoutSeconds: 10)

        print(outputURL.path)
        fflush(stdout)

        await SignalBox.shared.wait()

        do {
            try stream.removeRecordingOutput(recordingOutput)
            try await delegate.waitForFinish(timeoutSeconds: 15)
        } catch {
            try? await stream.stopCapture()
            throw error
        }
        try? await stream.stopCapture()
    }

    static func streamPCM(filter: SCContentFilter, configuration: SCStreamConfiguration, tagged: Bool) async throws {
        let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
        let queue = DispatchQueue(label: "codex-meeting-recorder.audio-output")
        let systemOutput = PCMStreamOutput(source: "system", tagged: tagged)
        let microphoneOutput = PCMStreamOutput(source: "microphone", tagged: tagged)

        if configuration.capturesAudio {
            try stream.addStreamOutput(systemOutput, type: .audio, sampleHandlerQueue: queue)
        }
        if configuration.captureMicrophone {
            try stream.addStreamOutput(microphoneOutput, type: .microphone, sampleHandlerQueue: queue)
        }

        installSignalHandler()
        try await stream.startCapture()
        fputs("pcm_stream_started\n", stderr)
        await SignalBox.shared.wait()
        try? await stream.stopCapture()
        fputs("pcm_stream_finished\n", stderr)
    }

    static func probeAudio(filter: SCContentFilter, configuration: SCStreamConfiguration, durationSeconds: Double) async throws {
        let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
        let queue = DispatchQueue(label: "codex-meeting-recorder.audio-probe")
        var outputs: [AudioProbeOutput] = []

        if configuration.capturesAudio {
            let systemOutput = AudioProbeOutput(source: "system")
            try stream.addStreamOutput(systemOutput, type: .audio, sampleHandlerQueue: queue)
            outputs.append(systemOutput)
        }
        if configuration.captureMicrophone {
            let microphoneOutput = AudioProbeOutput(source: "microphone")
            try stream.addStreamOutput(microphoneOutput, type: .microphone, sampleHandlerQueue: queue)
            outputs.append(microphoneOutput)
        }

        try await stream.startCapture()
        fputs("audio_probe_started\n", stderr)
        try await Task.sleep(nanoseconds: UInt64(durationSeconds * 1_000_000_000))
        try? await stream.stopCapture()
        fputs("audio_probe_finished\n", stderr)

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(outputs.map { $0.result() })
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
