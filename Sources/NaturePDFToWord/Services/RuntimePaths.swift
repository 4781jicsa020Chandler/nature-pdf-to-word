import Foundation

struct PythonInvocation: Sendable {
    let executableURL: URL
    let prefixArguments: [String]
    let environmentOverrides: [String: String]
}

struct RuntimePaths: Sendable {
    let appSupportRoot: URL
    let runtimeRoot: URL
    let workRoot: URL
    let logsRoot: URL

    var managedPythonURL: URL {
        runtimeRoot.appending(path: "venv/bin/python3")
    }

    static func make(fileManager: FileManager = .default) throws -> RuntimePaths {
        guard let supportDirectory = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            throw RuntimePathError.applicationSupportUnavailable
        }

        let appSupportRoot = supportDirectory.appending(path: "NaturePDFToWord", directoryHint: .isDirectory)
        return RuntimePaths(
            appSupportRoot: appSupportRoot,
            runtimeRoot: appSupportRoot.appending(path: "runtime", directoryHint: .isDirectory),
            workRoot: appSupportRoot.appending(path: "work", directoryHint: .isDirectory),
            logsRoot: appSupportRoot.appending(path: "logs", directoryHint: .isDirectory)
        )
    }

    func ensureDirectories(fileManager: FileManager = .default) throws {
        try fileManager.createDirectory(at: appSupportRoot, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: runtimeRoot, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: workRoot, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: logsRoot, withIntermediateDirectories: true)
    }

    static func backendScriptURL(
        fileManager: FileManager = .default,
        searchRoots: [URL]? = nil
    ) throws -> URL {
        let roots = searchRoots ?? defaultBackendSearchRoots()

        for root in roots {
            for relativePath in backendScriptRelativeCandidates {
                let candidate = root.appending(path: relativePath)
                if fileManager.fileExists(atPath: candidate.path) {
                    return candidate
                }
            }
        }

        for root in roots {
            if let discovered = firstBackendScript(under: root, fileManager: fileManager) {
                return discovered
            }
        }

        throw RuntimePathError.missingBackendScript
    }

    func preferredPythonInvocation(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        mainResourcesURL: URL? = Bundle.main.resourceURL,
        privateFrameworksURL: URL? = Bundle.main.privateFrameworksURL
    ) -> PythonInvocation {
        if FileManager.default.isExecutableFile(atPath: managedPythonURL.path) {
            return PythonInvocation(
                executableURL: managedPythonURL,
                prefixArguments: [],
                environmentOverrides: bundledPythonEnvironment(
                    mainResourcesURL: mainResourcesURL,
                    privateFrameworksURL: privateFrameworksURL
                ) ?? [:]
            )
        }

        if let override = environment["NATURE_PDF_TO_WORD_PYTHON"], !override.isEmpty {
            return PythonInvocation(
                executableURL: URL(fileURLWithPath: override),
                prefixArguments: [],
                environmentOverrides: [:]
            )
        }

        if let bundled = bundledPythonURL(
            mainResourcesURL: mainResourcesURL,
            privateFrameworksURL: privateFrameworksURL
        ), FileManager.default.isExecutableFile(atPath: bundled.path) {
            return PythonInvocation(
                executableURL: bundled,
                prefixArguments: [],
                environmentOverrides: bundledPythonEnvironment(
                    mainResourcesURL: mainResourcesURL,
                    privateFrameworksURL: privateFrameworksURL
                ) ?? [:]
            )
        }

        return PythonInvocation(
            executableURL: URL(fileURLWithPath: "/usr/bin/env"),
            prefixArguments: ["python3"],
            environmentOverrides: [:]
        )
    }

    func bundledPythonURL(
        mainResourcesURL: URL? = Bundle.main.resourceURL,
        privateFrameworksURL: URL? = Bundle.main.privateFrameworksURL
    ) -> URL? {
        let candidates = [
            mainResourcesURL?.appending(path: "EmbeddedPython/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"),
            mainResourcesURL?.appending(path: "EmbeddedPython/bin/python3.12"),
            mainResourcesURL?.appending(path: "EmbeddedPython/bin/python3"),
            privateFrameworksURL?.appending(path: "Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"),
            privateFrameworksURL?.appending(path: "Python.framework/Versions/3.12/bin/python3.12"),
        ]

        return candidates.compactMap { $0 }.first
    }

    func bundledPythonEnvironment(
        mainResourcesURL: URL? = Bundle.main.resourceURL,
        privateFrameworksURL: URL? = Bundle.main.privateFrameworksURL
    ) -> [String: String]? {
        guard let versionRoot = bundledPythonVersionRootURL(
            mainResourcesURL: mainResourcesURL,
            privateFrameworksURL: privateFrameworksURL
        ) else {
            return nil
        }

        let frameworkContainer = versionRoot
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()

        return [
            "DYLD_FRAMEWORK_PATH": frameworkContainer.path,
            "DYLD_LIBRARY_PATH": versionRoot.appending(path: "lib").path,
            "PYTHONHOME": versionRoot.path,
        ]
    }

    private func bundledPythonVersionRootURL(
        mainResourcesURL: URL?,
        privateFrameworksURL: URL?
    ) -> URL? {
        let candidates = [
            mainResourcesURL?.appending(path: "EmbeddedPython/Python.framework/Versions/3.12"),
            privateFrameworksURL?.appending(path: "Python.framework/Versions/3.12"),
        ]

        return candidates.compactMap { $0 }.first {
            FileManager.default.fileExists(atPath: $0.path)
        }
    }

    private static let backendScriptRelativeCandidates = [
        "Backend/nature_pdf_backend.py",
        "Resources/Backend/nature_pdf_backend.py",
        "NaturePDFToWord_NaturePDFToWord.bundle/Resources/Backend/nature_pdf_backend.py",
        "NaturePDFToWord_NaturePDFToWord.bundle/Contents/Resources/Backend/nature_pdf_backend.py",
    ]

    private static func defaultBackendSearchRoots() -> [URL] {
        var roots: [URL] = []

        if let resourceURL = Bundle.main.resourceURL {
            roots.append(resourceURL)
        }

        let bundleURL = Bundle.main.bundleURL
        roots.append(bundleURL)
        roots.append(bundleURL.deletingLastPathComponent())

        if let executableURL = Bundle.main.executableURL {
            let executableDirectory = executableURL.deletingLastPathComponent()
            roots.append(executableDirectory)
            roots.append(executableDirectory.deletingLastPathComponent())
            roots.append(executableDirectory.deletingLastPathComponent().deletingLastPathComponent())
        }

        var uniqueRoots: [URL] = []
        var seenPaths = Set<String>()
        for root in roots {
            if seenPaths.insert(root.path).inserted {
                uniqueRoots.append(root)
            }
        }

        return uniqueRoots
    }

    private static func firstBackendScript(under root: URL, fileManager: FileManager) -> URL? {
        guard let enumerator = fileManager.enumerator(
            at: root,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else {
            return nil
        }

        for case let candidate as URL in enumerator {
            guard candidate.lastPathComponent == "nature_pdf_backend.py" else {
                continue
            }

            guard candidate.path.contains("/Backend/") else {
                continue
            }

            return candidate
        }

        return nil
    }
}

enum RuntimePathError: LocalizedError {
    case applicationSupportUnavailable
    case missingBackendScript

    var errorDescription: String? {
        switch self {
        case .applicationSupportUnavailable:
            "Application Support could not be located for the current user."
        case .missingBackendScript:
            "The bundled Python backend script could not be found."
        }
    }
}
