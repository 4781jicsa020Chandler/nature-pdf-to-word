import Foundation

enum CleanupProfile: String, Codable, CaseIterable, Sendable {
    case natureMetadataOnly = "nature_metadata_only"
}

enum BackendKind: String, Codable, CaseIterable, Sendable {
    case pipeline
}

enum ConversionWarning: String, Codable, CaseIterable, Hashable, Sendable {
    case usedImageFallback
    case publisherMetadataRemoved
    case emptyTextPageRecovered
    case parseFailed
    case docxExportFailed

    var displayLabel: String {
        switch self {
        case .usedImageFallback:
            "Image fallback"
        case .publisherMetadataRemoved:
            "Metadata removed"
        case .emptyTextPageRecovered:
            "Recovered empty-text pages"
        case .parseFailed:
            "Parse failed"
        case .docxExportFailed:
            "DOCX export failed"
        }
    }
}

enum ConversionOutcome: String, Codable, Sendable {
    case succeeded
    case failed
    case cancelled
}

struct ConversionRequest: Codable, Sendable {
    var inputPDFPaths: [String]
    var exportDirectory: String
    var cleanupProfile: CleanupProfile = .natureMetadataOnly
    var backend: BackendKind = .pipeline
}

struct ConversionResult: Codable, Sendable {
    var sourcePdfPath: String
    var status: ConversionOutcome
    var outputDocxPath: String?
    var warnings: [ConversionWarning]
    var message: String?
}

enum JobStatus: String, Codable, Sendable {
    case queued
    case preparingRuntime
    case running
    case succeeded
    case failed
    case cancelled

    var displayName: String {
        switch self {
        case .queued:
            "Queued"
        case .preparingRuntime:
            "Preparing Runtime"
        case .running:
            "Running"
        case .succeeded:
            "Succeeded"
        case .failed:
            "Failed"
        case .cancelled:
            "Cancelled"
        }
    }
}

struct ConversionJob: Identifiable, Sendable {
    let id: UUID
    let sourcePDF: URL
    var status: JobStatus
    var outputDocx: URL?
    var warnings: [ConversionWarning]
    var message: String?
    var progressLog: [String]

    init(id: UUID = UUID(), sourcePDF: URL) {
        self.id = id
        self.sourcePDF = sourcePDF
        self.status = .queued
        self.outputDocx = nil
        self.warnings = []
        self.message = nil
        self.progressLog = []
    }

    var displayName: String {
        sourcePDF.lastPathComponent
    }

    var latestProgress: String? {
        progressLog.last
    }
}
