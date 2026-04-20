import Foundation

actor BackendCommandRunner {
    private var currentProcess: Process?

    func ensureRuntime(paths: RuntimePaths, report: @escaping @Sendable (String) -> Void) async throws {
        let python = paths.preferredPythonInvocation()
        _ = try await runPython(
            python: python,
            scriptArguments: [
                "ensure-runtime",
                "--runtime-root", paths.runtimeRoot.path,
            ],
            report: report
        )
    }

    func convert(
        request: ConversionRequest,
        inputFile: URL,
        outputFile: URL,
        paths: RuntimePaths,
        report: @escaping @Sendable (String) -> Void
    ) async throws -> ConversionResult {
        let python = paths.preferredPythonInvocation()
        let stdout = try await runPython(
            python: python,
            scriptArguments: [
                "convert",
                "--runtime-root", paths.runtimeRoot.path,
                "--input", inputFile.path,
                "--output-path", outputFile.path,
                "--backend", request.backend.rawValue,
                "--cleanup-profile", request.cleanupProfile.rawValue,
            ],
            report: report
        )

        let decoder = JSONDecoder()
        return try decoder.decode(ConversionResult.self, from: stdout)
    }

    func cancelCurrent() {
        currentProcess?.terminate()
    }

    private func runPython(
        python: PythonInvocation,
        scriptArguments: [String],
        report: @escaping @Sendable (String) -> Void
    ) async throws -> Data {
        let backendScript = try RuntimePaths.backendScriptURL()
        let process = Process()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        let stdoutCollector = StreamCollector()
        let stderrCollector = StreamCollector(report: report)

        process.executableURL = python.executableURL
        process.arguments = python.prefixArguments + [backendScript.path] + scriptArguments
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        for (key, value) in python.environmentOverrides {
            environment[key] = value
        }
        process.environment = environment

        stdoutPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty {
                stdoutCollector.consume(data)
            }
        }
        stderrPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty {
                stderrCollector.consume(data)
            }
        }

        report("Using Python: \(python.executableURL.path)")
        report("Backend command: \(scriptArguments.joined(separator: " "))")

        do {
            try process.run()
            currentProcess = process
        } catch {
            stdoutPipe.fileHandleForReading.readabilityHandler = nil
            stderrPipe.fileHandleForReading.readabilityHandler = nil
            throw error
        }

        let exitCode = await Task.detached(priority: .userInitiated) { () -> Int32 in
            process.waitUntilExit()
            return process.terminationStatus
        }.value

        currentProcess = nil
        stdoutPipe.fileHandleForReading.readabilityHandler = nil
        stderrPipe.fileHandleForReading.readabilityHandler = nil
        stdoutCollector.consume(stdoutPipe.fileHandleForReading.readDataToEndOfFile())
        stderrCollector.consume(stderrPipe.fileHandleForReading.readDataToEndOfFile())
        stderrCollector.finish()

        if exitCode == 0 {
            return stdoutCollector.data
        }

        throw BackendCommandError.commandFailed(
            exitCode: exitCode,
            stderr: stderrCollector.text.isEmpty ? "The backend exited with no error output." : stderrCollector.text
        )
    }
}

enum BackendCommandError: LocalizedError {
    case commandFailed(exitCode: Int32, stderr: String)

    var errorDescription: String? {
        switch self {
        case let .commandFailed(exitCode, stderr):
            "Backend command failed with exit code \(exitCode): \(stderr)"
        }
    }
}

private final class StreamCollector: @unchecked Sendable {
    private let queue = DispatchQueue(label: "NaturePDFToWord.StreamCollector")
    private var storage = Data()
    private var bufferedText = ""
    private let report: (@Sendable (String) -> Void)?

    init(report: (@Sendable (String) -> Void)? = nil) {
        self.report = report
    }

    func consume(_ data: Data) {
        guard !data.isEmpty else { return }
        queue.sync {
            storage.append(data)
            guard let report else { return }
            bufferedText.append(String(decoding: data, as: UTF8.self))

            while let newlineRange = bufferedText.range(of: "\n") {
                let line = String(bufferedText[..<newlineRange.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
                bufferedText.removeSubrange(..<newlineRange.upperBound)
                if !line.isEmpty {
                    report(line)
                }
            }
        }
    }

    func finish() {
        queue.sync {
            let line = bufferedText.trimmingCharacters(in: .whitespacesAndNewlines)
            if !line.isEmpty {
                report?(line)
            }
            bufferedText = ""
        }
    }

    var data: Data {
        queue.sync { storage }
    }

    var text: String {
        queue.sync { String(decoding: storage, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines) }
    }
}
