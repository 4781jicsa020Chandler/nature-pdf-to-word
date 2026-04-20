import SwiftUI

struct ContentView: View {
    @ObservedObject var viewModel: AppViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            controls
            content
        }
        .padding(20)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Nature PDF to Word")
                .font(.system(size: 30, weight: .bold))

            Text("Batch-convert Nature PDFs into cleaned Word files using a managed MinerU backend, Nature-specific metadata cleanup, and DOCX export.")
                .foregroundStyle(.secondary)

            HStack(spacing: 18) {
                Label("\(viewModel.selectedPDFs.count) PDF(s) selected", systemImage: "doc.on.doc")
                Label(viewModel.exportDirectory?.path ?? "No export folder selected", systemImage: "folder")
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
        }
    }

    private var controls: some View {
        GroupBox {
            HStack(alignment: .top, spacing: 12) {
                Button("Select PDFs") {
                    viewModel.choosePDFs()
                }

                Button("Choose Export Folder") {
                    viewModel.chooseExportDirectory()
                }

                Button("Start Batch") {
                    viewModel.startBatch()
                }
                .buttonStyle(.borderedProminent)
                .disabled(!viewModel.canStart)

                Button("Cancel Current") {
                    viewModel.cancelCurrent()
                }
                .disabled(!viewModel.isRunning)

                Button("Open Export Folder") {
                    viewModel.openExportFolder()
                }
                .disabled(viewModel.exportDirectory == nil)

                Spacer()

                VStack(alignment: .trailing, spacing: 4) {
                    Text(viewModel.setupStatus)
                        .font(.headline)
                    if let lastError = viewModel.lastError {
                        Text(lastError)
                            .foregroundStyle(.red)
                            .font(.subheadline)
                    } else {
                        Text(
                            viewModel.runtimeReady
                            ? "Managed runtime ready"
                            : "Bundled Python included. MinerU models and Pandoc are prepared on first run."
                        )
                            .foregroundStyle(.secondary)
                            .font(.subheadline)
                    }
                }
                .multilineTextAlignment(.trailing)
            }
        }
    }

    private var content: some View {
        HStack(alignment: .top, spacing: 16) {
            GroupBox("Batch Queue") {
                if viewModel.jobs.isEmpty {
                    ContentUnavailableView(
                        "No PDFs Selected",
                        systemImage: "doc.text.magnifyingglass",
                        description: Text("Choose one or more Nature PDFs to populate the batch queue.")
                    )
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List {
                        ForEach(viewModel.jobs) { job in
                            JobRowView(
                                job: job,
                                retryAction: { viewModel.retry(job.id) },
                                revealAction: { viewModel.revealOutput(for: job) }
                            )
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .frame(minWidth: 700, maxWidth: .infinity, minHeight: 560)

            GroupBox("Backend Log") {
                ScrollView {
                    Text(viewModel.globalLog.joined(separator: "\n"))
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                        .textSelection(.enabled)
                        .padding(8)
                }
                .background(Color(NSColor.textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
            .frame(width: 360)
            .frame(minHeight: 560)
        }
    }
}

private struct JobRowView: View {
    let job: ConversionJob
    let retryAction: () -> Void
    let revealAction: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(job.displayName)
                        .font(.headline)
                    Text(job.sourcePDF.path)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                Spacer()

                Text(job.status.displayName)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(statusColor.opacity(0.18))
                    .foregroundStyle(statusColor)
                    .clipShape(Capsule())
            }

            if let message = job.message {
                Text(message)
                    .font(.subheadline)
            }

            if !job.warnings.isEmpty {
                FlowLayout {
                    ForEach(job.warnings.sorted(by: { $0.rawValue < $1.rawValue }), id: \.self) { warning in
                        Text(warning.displayLabel)
                            .font(.caption)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Color.orange.opacity(0.18))
                            .foregroundStyle(.orange)
                            .clipShape(Capsule())
                    }
                }
            }

            if let output = job.outputDocx {
                Text(output.path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }

            HStack {
                if job.status == .failed || job.status == .cancelled {
                    Button("Retry", action: retryAction)
                        .buttonStyle(.borderless)
                }

                if job.outputDocx != nil && job.status == .succeeded {
                    Button("Reveal in Finder", action: revealAction)
                        .buttonStyle(.borderless)
                }

                Spacer()
            }
        }
        .padding(.vertical, 6)
    }

    private var statusColor: Color {
        switch job.status {
        case .queued, .preparingRuntime:
            .blue
        case .running:
            .teal
        case .succeeded:
            .green
        case .failed:
            .red
        case .cancelled:
            .orange
        }
    }
}

private struct FlowLayout<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
    }
}
