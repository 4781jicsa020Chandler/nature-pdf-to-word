import Foundation
import Testing
@testable import NaturePDFToWord

@Test
func outputNamingUsesFinderStyleSuffixes() throws {
    let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }

    FileManager.default.createFile(atPath: root.appending(path: "Linear RAG.docx").path, contents: Data())
    FileManager.default.createFile(atPath: root.appending(path: "Linear RAG (2).docx").path, contents: Data())

    let candidate = OutputNaming.uniqueFileURL(stem: "Linear RAG", fileExtension: "docx", in: root)
    #expect(candidate.lastPathComponent == "Linear RAG (3).docx")
}

@Test
func conversionRequestDefaultsMatchPublicContract() {
    let request = ConversionRequest(inputPDFPaths: ["/tmp/example.pdf"], exportDirectory: "/tmp/out")
    #expect(request.cleanupProfile == .natureMetadataOnly)
    #expect(request.backend == .pipeline)
}
