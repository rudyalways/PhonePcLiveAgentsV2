#!/usr/bin/env swift
/**
 * ScreenCaptureKit-based visual test feeder for omni agent testing.
 *
 * High-performance continuous screen capture using Apple's native framework.
 * Designed as a test framework component, separate from production code.
 *
 * Features:
 * - Hardware-accelerated capture (2-5% CPU vs 10-15% for CLI screencapture)
 * - Direct memory streaming (zero disk I/O)
 * - Configurable frame rate (default 0.2 fps for tests)
 * - WebSocket output to omni-exp agent
 *
 * Requirements: macOS 12.3+, Screen Recording permission
 *
 * Usage:
 *   swift tests/screencapturekit-feeder.swift --fps 0.2 --port 7090
 */

import Foundation
import ScreenCaptureKit
import AppKit

// MARK: - Configuration

struct FeederConfig {
    let fps: Double
    let omniHost: String
    let omniPort: Int
    let quality: CGFloat
    let maxWidth: Int

    static let `default` = FeederConfig(
        fps: 0.5,           // 1 frame every 2 seconds (test default)
        omniHost: "localhost",
        omniPort: 7090,
        quality: 0.85,
        maxWidth: 1280      // Downscale for bandwidth
    )
}

// MARK: - Statistics

class FeederStats {
    private(set) var framesSent: Int = 0
    private(set) var framesFailed: Int = 0
    private(set) var bytesTotal: Int64 = 0
    private let startTime = Date()

    var duration: TimeInterval {
        Date().timeIntervalSince(startTime)
    }

    var avgFPS: Double {
        duration > 0 ? Double(framesSent) / duration : 0
    }

    func recordSuccess(bytes: Int) {
        framesSent += 1
        bytesTotal += Int64(bytes)
    }

    func recordFailure() {
        framesFailed += 1
    }

    func summary() -> String {
        """
        Test Run Statistics:
          Frames sent: \(framesSent)
          Frames failed: \(framesFailed)
          Duration: \(String(format: "%.1f", duration))s
          Average FPS: \(String(format: "%.3f", avgFPS))
          Total data: \(String(format: "%.2f", Double(bytesTotal) / 1_048_576))MB
        """
    }
}

// MARK: - ScreenCaptureKit Feeder

@available(macOS 12.3, *)
class ScreenCaptureKitFeeder: NSObject {
    private let config: FeederConfig
    private let stats = FeederStats()
    private var stream: SCStream?
    private var timer: Timer?
    private var latestFrame: Data?
    private var wsTask: URLSessionWebSocketTask?
    private var isRunning = false

    init(config: FeederConfig = .default) {
        self.config = config
        super.init()
    }

    func start() async throws {
        print("[ScreenCaptureKit] Starting feeder...")
        print("  FPS: \(config.fps) (interval: \(1.0/config.fps)s)")
        print("  Target: \(config.omniHost):\(config.omniPort)")
        print("  Quality: \(Int(config.quality * 100))%")

        // Connect WebSocket
        try await connectWebSocket()

        // Get main display
        let content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: true
        )

        guard let display = content.displays.first else {
            throw FeederError.noDisplay
        }

        print("  Display: \(display.width)x\(display.height)")

        // Configure stream
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let streamConfig = SCStreamConfiguration()
        streamConfig.width = min(display.width, config.maxWidth)
        streamConfig.height = Int(Double(display.height) * (Double(streamConfig.width) / Double(display.width)))
        streamConfig.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(config.fps))
        streamConfig.queueDepth = 3
        streamConfig.showsCursor = true
        streamConfig.pixelFormat = kCVPixelFormatType_32BGRA

        // Create and start stream
        stream = SCStream(filter: filter, configuration: streamConfig, delegate: nil)

        try stream?.addStreamOutput(
            self,
            type: .screen,
            sampleHandlerQueue: .global(qos: .userInitiated)
        )

        try await stream?.startCapture()

        isRunning = true

        // Start periodic frame sender (samples the latest captured frame)
        let interval = 1.0 / config.fps
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { await self?.sendLatestFrame() }
        }

        print("[ScreenCaptureKit] Capture started\n")
    }

    func stop() async {
        print("\n[ScreenCaptureKit] Stopping feeder...")
        isRunning = false

        timer?.invalidate()
        timer = nil

        if let stream = stream {
            try? await stream.stopCapture()
        }
        stream = nil

        wsTask?.cancel(with: .goingAway, reason: nil)
        wsTask = nil

        print(stats.summary())
    }

    private func connectWebSocket() async throws {
        let url = URL(string: "wss://\(config.omniHost):\(config.omniPort)/ws")!
        var request = URLRequest(url: url)
        request.timeoutInterval = 10

        // The agent uses a self-signed cert (state/server.crt) — accept it
        // for this localhost-only test tool.
        let session = URLSession(
            configuration: .default,
            delegate: InsecureTLSDelegate(),
            delegateQueue: nil
        )
        wsTask = session.webSocketTask(with: request)
        wsTask?.resume()

        // Mandatory handshake — the agent rejects everything until
        // session.start arrives (see omni-exp-agent.py ws_handler).
        let start: [String: Any] = ["type": "session.start", "user": "sck-feeder", "auth": ""]
        let startData = try JSONSerialization.data(withJSONObject: start)
        try await wsTask?.send(.string(String(data: startData, encoding: .utf8)!))

        print("  WebSocket: connected, session.start sent")
    }

    private func sendLatestFrame() async {
        guard isRunning,
              let frameData = latestFrame,
              let wsTask = wsTask else {
            return
        }

        do {
            // The agent accepts only TEXT JSON frames; WS binary is rejected.
            let payload: [String: Any] = [
                "type": "image",
                "mime": "image/jpeg",
                "data": frameData.base64EncodedString(),
            ]
            let json = try JSONSerialization.data(withJSONObject: payload)
            try await wsTask.send(.string(String(data: json, encoding: .utf8)!))
            stats.recordSuccess(bytes: frameData.count)

            if stats.framesSent % 10 == 0 {
                print("  \(stats.framesSent) frames sent (\(String(format: "%.1f", stats.duration))s)")
            }
        } catch {
            stats.recordFailure()
            print("  Frame send failed: \(error.localizedDescription)")
        }
    }
}

// MARK: - SCStreamOutput

@available(macOS 12.3, *)
extension ScreenCaptureKitFeeder: SCStreamOutput {
    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .screen,
              let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            return
        }

        // Convert to JPEG in background
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self = self else { return }

            CVPixelBufferLockBaseAddress(imageBuffer, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(imageBuffer, .readOnly) }

            let ciImage = CIImage(cvPixelBuffer: imageBuffer)
            let context = CIContext()

            guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else {
                return
            }

            let nsImage = NSImage(cgImage: cgImage, size: .zero)
            guard let tiffData = nsImage.tiffRepresentation,
                  let bitmapImage = NSBitmapImageRep(data: tiffData),
                  let jpegData = bitmapImage.representation(
                    using: .jpeg,
                    properties: [.compressionFactor: self.config.quality]
                  ) else {
                return
            }

            self.latestFrame = jpegData
        }
    }
}

// MARK: - TLS (test-only)

/// Accepts the agent's self-signed localhost certificate. Test-only.
final class InsecureTLSDelegate: NSObject, URLSessionDelegate {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        if let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}

// MARK: - Error Types

enum FeederError: Error {
    case noDisplay
    case webSocketFailed
}

// MARK: - CLI Entry Point

@available(macOS 12.3, *)
struct ScreenCaptureKitFeederCLI {
    static func main() async {
        let args = CommandLine.arguments
        var fps = FeederConfig.default.fps
        var port = FeederConfig.default.omniPort

        // Parse arguments
        for i in 0..<args.count {
            if args[i] == "--fps" && i + 1 < args.count {
                fps = Double(args[i + 1]) ?? fps
            } else if args[i] == "--port" && i + 1 < args.count {
                port = Int(args[i + 1]) ?? port
            } else if args[i] == "--help" {
                printUsage()
                return
            }
        }

        let config = FeederConfig(
            fps: fps,
            omniHost: "localhost",
            omniPort: port,
            quality: 0.85,
            maxWidth: 1280
        )

        let feeder = ScreenCaptureKitFeeder(config: config)

        // Handle shutdown signal. A plain signal() C-function-pointer
        // cannot capture `feeder`; use a DispatchSourceSignal instead.
        signal(SIGINT, SIG_IGN)
        let sigSrc = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        sigSrc.setEventHandler {
            Task {
                await feeder.stop()
                exit(0)
            }
        }
        sigSrc.resume()

        do {
            try await feeder.start()

            // Run until interrupted
            RunLoop.main.run()
        } catch {
            print("Error: \(error.localizedDescription)")
            exit(1)
        }
    }

    static func printUsage() {
        print("""
        ScreenCaptureKit Test Feeder

        Usage: swift screencapturekit-feeder.swift [options]

        Options:
          --fps RATE     Frame rate (default: 0.2 = 1 frame every 5s)
          --port PORT    Omni-exp WebSocket port (default: 7090)
          --help         Show this help

        Examples:
          # Test mode: 1 frame every 5 seconds
          swift screencapturekit-feeder.swift --fps 0.2

          # Faster: 1 frame every 2 seconds
          swift screencapturekit-feeder.swift --fps 0.5

          # High rate for integration tests
          swift screencapturekit-feeder.swift --fps 2.0
        """)
    }
}


// Script-mode entry point (@main is illegal when the file has top-level code).
if #available(macOS 12.3, *) {
    await ScreenCaptureKitFeederCLI.main()
} else {
    print("Error: macOS 12.3+ required for ScreenCaptureKit")
    exit(1)
}
