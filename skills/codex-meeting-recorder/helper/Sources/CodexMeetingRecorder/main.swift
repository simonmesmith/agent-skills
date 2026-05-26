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

    var description: String {
        switch self {
        case .usage:
            return "Usage: codex-meeting-recorder record --out <path.mp4> [--no-system-audio] [--no-microphone]"
        case .unsupportedOS:
            return "Codex Meeting Recorder requires macOS 15 or newer."
        case .noDisplay:
            return "No capturable display was found."
        case .permissionDenied(let detail):
            return detail
        case .recordingFailed(let detail):
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
    var outputPath: String?
    var captureSystemAudio = true
    var captureMicrophone = true
}

func parseArguments(_ args: [String]) throws -> Arguments {
    guard args.first == "record" else {
        throw RecorderError.usage
    }

    var parsed = Arguments()
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
        case "--help", "-h":
            throw RecorderError.usage
        default:
            throw RecorderError.usage
        }
        index += 1
    }

    guard parsed.outputPath != nil else {
        throw RecorderError.usage
    }
    return parsed
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
        guard let outputPath = args.outputPath else {
            throw RecorderError.usage
        }

        if args.captureSystemAudio && !requestScreenCapturePermissionIfNeeded() {
            throw RecorderError.permissionDenied("Screen Recording permission is required for system audio. Grant it in System Settings, restart Codex/Terminal if macOS asks, then retry.")
        }

        if args.captureMicrophone {
            let micGranted = await requestMicrophonePermissionIfNeeded()
            if !micGranted {
                throw RecorderError.permissionDenied("Microphone permission is required. Grant it in System Settings, restart Codex/Terminal if macOS asks, then retry.")
            }
        }

        let outputURL = URL(fileURLWithPath: outputPath)
        try FileManager.default.createDirectory(
            at: outputURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        if FileManager.default.fileExists(atPath: outputURL.path) {
            try FileManager.default.removeItem(at: outputURL)
        }

        let content = try await SCShareableContent.current
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
}
