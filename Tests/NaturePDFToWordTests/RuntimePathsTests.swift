import Foundation
import Testing
@testable import NaturePDFToWord

@Test
func preferredPythonInvocationUsesManagedRuntimeWhenPresent() throws {
    let root = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: root) }

    let paths = makeRuntimePaths(root: root)
    try createExecutable(at: paths.managedPythonURL)

    let invocation = paths.preferredPythonInvocation(
        environment: [:],
        mainResourcesURL: nil,
        privateFrameworksURL: nil
    )

    #expect(invocation.executableURL == paths.managedPythonURL)
    #expect(invocation.prefixArguments.isEmpty)
    #expect(invocation.environmentOverrides.isEmpty)
}

@Test
func preferredPythonInvocationUsesBundledPythonWhenManagedRuntimeIsMissing() throws {
    let root = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: root) }

    let paths = makeRuntimePaths(root: root)
    let resourcesRoot = root.appending(path: "bundle-resources", directoryHint: .isDirectory)
    let versionRoot = resourcesRoot.appending(path: "EmbeddedPython/Python.framework/Versions/3.12", directoryHint: .isDirectory)
    let bundledPython = versionRoot.appending(path: "Resources/Python.app/Contents/MacOS/Python")
    try createExecutable(at: bundledPython)

    let invocation = paths.preferredPythonInvocation(
        environment: [:],
        mainResourcesURL: resourcesRoot,
        privateFrameworksURL: nil
    )

    #expect(invocation.executableURL == bundledPython)
    #expect(invocation.prefixArguments.isEmpty)
    #expect(invocation.environmentOverrides["PYTHONHOME"] == versionRoot.path)
    #expect(invocation.environmentOverrides["DYLD_LIBRARY_PATH"] == versionRoot.appending(path: "lib").path)
    #expect(invocation.environmentOverrides["DYLD_FRAMEWORK_PATH"] == resourcesRoot.appending(path: "EmbeddedPython").path)
}

@Test
func preferredPythonInvocationUsesManagedRuntimeWithBundledPythonEnvironment() throws {
    let root = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: root) }

    let paths = makeRuntimePaths(root: root)
    let resourcesRoot = root.appending(path: "bundle-resources", directoryHint: .isDirectory)
    let versionRoot = resourcesRoot.appending(path: "EmbeddedPython/Python.framework/Versions/3.12", directoryHint: .isDirectory)
    let bundledPython = versionRoot.appending(path: "Resources/Python.app/Contents/MacOS/Python")
    try createExecutable(at: bundledPython)
    try createExecutable(at: paths.managedPythonURL)

    let invocation = paths.preferredPythonInvocation(
        environment: [:],
        mainResourcesURL: resourcesRoot,
        privateFrameworksURL: nil
    )

    #expect(invocation.executableURL == paths.managedPythonURL)
    #expect(invocation.prefixArguments.isEmpty)
    #expect(invocation.environmentOverrides["PYTHONHOME"] == versionRoot.path)
    #expect(invocation.environmentOverrides["DYLD_LIBRARY_PATH"] == versionRoot.appending(path: "lib").path)
    #expect(invocation.environmentOverrides["DYLD_FRAMEWORK_PATH"] == resourcesRoot.appending(path: "EmbeddedPython").path)
}

@Test
func preferredPythonInvocationFallsBackToEnvironmentOverride() throws {
    let root = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: root) }

    let paths = makeRuntimePaths(root: root)
    let override = root.appending(path: "custom-python")
    try createExecutable(at: override)

    let invocation = paths.preferredPythonInvocation(
        environment: ["NATURE_PDF_TO_WORD_PYTHON": override.path],
        mainResourcesURL: nil,
        privateFrameworksURL: nil
    )

    #expect(invocation.executableURL == override)
    #expect(invocation.prefixArguments.isEmpty)
    #expect(invocation.environmentOverrides.isEmpty)
}

@Test
func preferredPythonInvocationFallsBackToEnvPython3() throws {
    let root = try temporaryDirectory()
    defer { try? FileManager.default.removeItem(at: root) }

    let paths = makeRuntimePaths(root: root)
    let invocation = paths.preferredPythonInvocation(
        environment: [:],
        mainResourcesURL: nil,
        privateFrameworksURL: nil
    )

    #expect(invocation.executableURL.path == "/usr/bin/env")
    #expect(invocation.prefixArguments == ["python3"])
    #expect(invocation.environmentOverrides.isEmpty)
}

private func makeRuntimePaths(root: URL) -> RuntimePaths {
    RuntimePaths(
        appSupportRoot: root,
        runtimeRoot: root.appending(path: "runtime", directoryHint: .isDirectory),
        workRoot: root.appending(path: "work", directoryHint: .isDirectory),
        logsRoot: root.appending(path: "logs", directoryHint: .isDirectory)
    )
}

private func temporaryDirectory() throws -> URL {
    let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    return directory
}

private func createExecutable(at url: URL) throws {
    try FileManager.default.createDirectory(
        at: url.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    FileManager.default.createFile(atPath: url.path, contents: Data("#!/bin/sh\n".utf8))
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
}
