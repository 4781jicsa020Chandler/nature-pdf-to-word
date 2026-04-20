import Foundation

enum OutputNaming {
    static func uniqueOutputURL(for inputPDF: URL, in directory: URL, fileManager: FileManager = .default) -> URL {
        uniqueFileURL(
            stem: inputPDF.deletingPathExtension().lastPathComponent,
            fileExtension: "docx",
            in: directory,
            fileManager: fileManager
        )
    }

    static func uniqueFileURL(stem: String, fileExtension: String, in directory: URL, fileManager: FileManager = .default) -> URL {
        let ext = fileExtension.hasPrefix(".") ? String(fileExtension.dropFirst()) : fileExtension
        let base = directory.appending(path: "\(stem).\(ext)")
        if !fileManager.fileExists(atPath: base.path) {
            return base
        }

        var index = 2
        while true {
            let candidate = directory.appending(path: "\(stem) (\(index)).\(ext)")
            if !fileManager.fileExists(atPath: candidate.path) {
                return candidate
            }
            index += 1
        }
    }
}
