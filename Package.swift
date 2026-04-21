// swift-tools-version: 6.2

import PackageDescription

let package = Package(
    name: "NaturePDFToWord",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(
            name: "NaturePDFToWord",
            targets: ["NaturePDFToWord"]
        ),
    ],
    targets: [
        .executableTarget(
            name: "NaturePDFToWord",
            resources: [
                .copy("Resources"),
            ]
        ),
        .testTarget(
            name: "NaturePDFToWordTests",
            dependencies: ["NaturePDFToWord"]
        ),
    ],
    swiftLanguageModes: [.v6]
)
