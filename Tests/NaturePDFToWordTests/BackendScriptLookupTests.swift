import Foundation
import Testing
@testable import NaturePDFToWord

@Test
func backendScriptURLFindsPackagedBundleLayout() throws {
    let root = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: root) }

    let script = root
        .appending(path: "NaturePDFToWord_NaturePDFToWord.bundle", directoryHint: .isDirectory)
        .appending(path: "Resources/Backend/nature_pdf_backend.py")

    try createFile(at: script)

    let resolved = try RuntimePaths.backendScriptURL(searchRoots: [root])
    #expect(resolved == script)
}

@Test
func backendScriptURLFindsDirectBackendLayout() throws {
    let root = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: root) }

    let script = root.appending(path: "Backend/nature_pdf_backend.py")
    try createFile(at: script)

    let resolved = try RuntimePaths.backendScriptURL(searchRoots: [root])
    #expect(resolved == script)
}

@Test
func backendScriptURLThrowsWhenMissing() throws {
    let root = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: root) }

    #expect(throws: RuntimePathError.missingBackendScript) {
        try RuntimePaths.backendScriptURL(searchRoots: [root])
    }
}

private func temporaryDirectory() throws -> URL {
    let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    return directory
}

private func createFile(at url: URL) throws {
    try FileManager.default.createDirectory(
        at: url.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    FileManager.default.createFile(atPath: url.path, contents: Data("#!/usr/bin/env python3\n".utf8))
}
