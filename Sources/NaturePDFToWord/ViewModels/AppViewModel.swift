import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

@MainActor
final class AppViewModel: ObservableObject {
    @Published var selectedPDFs: [URL] = []
    @Published var exportDirectory: URL?
    @Published var jobs: [ConversionJob] = []
    @Published var setupStatus = "Select one or more Nature PDFs to begin."
    @Published var isRunning = false
    @Published var runtimeReady = false
    @Published var globalLog: [String] = []
    @Published var lastError: String?

    private let runner = BackendCommandRunner()
    private var batchTask: Task<Void, Never>?
    private var cancelRequested = false

    init() {
        exportDirectory = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        if let paths = try? RuntimePaths.make(), paths.bundledPythonURL() != nil {
            setupStatus = "Bundled Python included. First launch will prepare MinerU and Pandoc in the managed runtime."
        }
    }

    var canStart: Bool {
        !selectedPDFs.isEmpty && exportDirectory != nil && !isRunning
    }

    func choosePDFs() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType.pdf]
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.prompt = "Select PDFs"

        guard panel.runModal() == .OK else { return }

        selectedPDFs = panel.urls.sorted { $0.lastPathComponent.localizedCaseInsensitiveCompare($1.lastPathComponent) == .orderedAscending }
        jobs = selectedPDFs.map { ConversionJob(sourcePDF: $0) }
        lastError = nil
        setupStatus = "Queued \(selectedPDFs.count) PDF(s). Choose an export folder and start the batch."
    }

    func chooseExportDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose Export Folder"

        if panel.runModal() == .OK {
            exportDirectory = panel.url
            lastError = nil
        }
    }

    func startBatch() {
        guard canStart, let exportDirectory else { return }
        cancelRequested = false
        lastError = nil

        batchTask?.cancel()
        batchTask = Task {
            await runBatch(exportDirectory: exportDirectory)
        }
    }

    func cancelCurrent() {
        guard isRunning else { return }
        cancelRequested = true
        setupStatus = "Cancelling current conversion..."
        batchTask?.cancel()
        Task {
            await runner.cancelCurrent()
        }
    }

    func retry(_ jobID: UUID) {
        guard let index = jobs.firstIndex(where: { $0.id == jobID }) else { return }
        jobs[index].status = .queued
        jobs[index].outputDocx = nil
        jobs[index].warnings = []
        jobs[index].message = nil
        jobs[index].progressLog = []
        lastError = nil
    }

    func revealOutput(for job: ConversionJob) {
        guard let url = job.outputDocx else { return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    func openExportFolder() {
        guard let exportDirectory else { return }
        NSWorkspace.shared.open(exportDirectory)
    }

    private func runBatch(exportDirectory: URL) async {
        isRunning = true
        defer {
            isRunning = false
            batchTask = nil
        }

        do {
            let paths = try RuntimePaths.make()
            try paths.ensureDirectories()

            updateAllQueuedJobs(to: .preparingRuntime)
            setupStatus = "Preparing the managed MinerU runtime..."

            try await runner.ensureRuntime(paths: paths) { [weak self] line in
                Task { @MainActor in
                    self?.record(line: line, jobID: nil)
                    self?.setupStatus = line
                }
            }

            runtimeReady = true
            setupStatus = "Runtime ready. Converting \(jobs.filter { $0.status == .queued || $0.status == .preparingRuntime }.count) file(s)..."

            for jobID in jobs.map(\.id) {
                if cancelRequested {
                    markRemainingQueuedJobsCancelled()
                    break
                }

                guard let index = jobs.firstIndex(where: { $0.id == jobID }) else { continue }
                if jobs[index].status == .succeeded {
                    continue
                }

                let sourcePDF = jobs[index].sourcePDF
                let outputURL = OutputNaming.uniqueOutputURL(for: sourcePDF, in: exportDirectory)
                let request = ConversionRequest(
                    inputPDFPaths: [sourcePDF.path],
                    exportDirectory: exportDirectory.path
                )

                jobs[index].status = .running
                jobs[index].message = "Submitting to MinerU..."
                jobs[index].progressLog = []
                jobs[index].warnings = []
                jobs[index].outputDocx = outputURL

                do {
                    let result = try await runner.convert(
                        request: request,
                        inputFile: sourcePDF,
                        outputFile: outputURL,
                        paths: paths
                    ) { [weak self] line in
                        Task { @MainActor in
                            self?.record(line: line, jobID: jobID)
                        }
                    }

                    guard let refreshedIndex = jobs.firstIndex(where: { $0.id == jobID }) else { continue }
                    jobs[refreshedIndex].status = result.status == .succeeded ? .succeeded : .failed
                    jobs[refreshedIndex].warnings = result.warnings
                    jobs[refreshedIndex].message = result.message ?? "Finished"
                    if let outputPath = result.outputDocxPath {
                        jobs[refreshedIndex].outputDocx = URL(fileURLWithPath: outputPath)
                    }
                    setupStatus = "Converted \(jobs.filter { $0.status == .succeeded }.count) of \(jobs.count) file(s)."
                } catch {
                    guard let refreshedIndex = jobs.firstIndex(where: { $0.id == jobID }) else { continue }
                    jobs[refreshedIndex].status = cancelRequested ? .cancelled : .failed
                    jobs[refreshedIndex].message = error.localizedDescription
                    if !jobs[refreshedIndex].warnings.contains(.parseFailed) {
                        jobs[refreshedIndex].warnings.append(.parseFailed)
                    }

                    if cancelRequested {
                        setupStatus = "Conversion cancelled."
                        markRemainingQueuedJobsCancelled()
                        break
                    }

                    lastError = error.localizedDescription
                    setupStatus = "A conversion failed. You can retry the failed item."
                }
            }

            if !cancelRequested {
                let successCount = jobs.filter { $0.status == .succeeded }.count
                setupStatus = "Finished. \(successCount) of \(jobs.count) file(s) converted."
            }
        } catch {
            lastError = error.localizedDescription
            setupStatus = "Runtime setup failed."
            updateAllQueuedJobs(to: .failed)
        }
    }

    private func updateAllQueuedJobs(to status: JobStatus) {
        for index in jobs.indices where jobs[index].status == .queued || jobs[index].status == .preparingRuntime {
            jobs[index].status = status
        }
    }

    private func markRemainingQueuedJobsCancelled() {
        for index in jobs.indices where jobs[index].status == .queued || jobs[index].status == .preparingRuntime {
            jobs[index].status = .cancelled
            jobs[index].message = "Cancelled before this file started."
        }
    }

    private func record(line: String, jobID: UUID?) {
        let timestamp = Self.timestampFormatter.string(from: Date())
        globalLog.append("[\(timestamp)] \(line)")
        if globalLog.count > 250 {
            globalLog.removeFirst(globalLog.count - 250)
        }

        guard let jobID, let index = jobs.firstIndex(where: { $0.id == jobID }) else { return }
        jobs[index].progressLog.append(line)
        if jobs[index].progressLog.count > 40 {
            jobs[index].progressLog.removeFirst(jobs[index].progressLog.count - 40)
        }
        jobs[index].message = line
    }

    private static let timestampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter
    }()
}
