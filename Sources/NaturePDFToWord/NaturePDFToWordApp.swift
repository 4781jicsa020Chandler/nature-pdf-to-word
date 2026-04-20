import SwiftUI

@main
struct NaturePDFToWordApp: App {
    @StateObject private var viewModel = AppViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView(viewModel: viewModel)
                .frame(minWidth: 1080, minHeight: 760)
        }
        .defaultSize(width: 1180, height: 800)
        .windowResizability(.contentSize)
    }
}
