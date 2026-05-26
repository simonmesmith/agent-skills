// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CodexMeetingRecorder",
    platforms: [
        .macOS(.v15)
    ],
    products: [
        .executable(name: "codex-meeting-recorder", targets: ["CodexMeetingRecorder"])
    ],
    targets: [
        .executableTarget(
            name: "CodexMeetingRecorder",
            swiftSettings: [
                .swiftLanguageMode(.v5)
            ],
            linkerSettings: [
                .linkedFramework("AVFoundation"),
                .linkedFramework("CoreGraphics"),
                .linkedFramework("CoreMedia"),
                .linkedFramework("ScreenCaptureKit")
            ]
        )
    ]
)
